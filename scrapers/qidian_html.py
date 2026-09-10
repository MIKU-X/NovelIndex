"""Parse the rendered Qidian document using Python's standard HTML parser.

The input contract is a public list/detail document, not an upstream crawler's
JavaScript object. Identity is taken from book links; tags require category links.
Missing fields stay empty. Protocol examples are synthetic in tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

ORIGIN = 'https://www.qidian.com/'
WORD_COUNT = re.compile(r'([0-9]+(?:\.[0-9]+)?)\s*([万亿]?)\s*字')


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def walk(self, tag=None, css_class=None):
        for child in self.children:
            if isinstance(child, Element):
                if (tag is None or child.tag == tag) and (css_class is None or css_class in child.attrs.get('class', '').split()):
                    yield child
                yield from child.walk(tag, css_class)

    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        return ' '.join(part.text() if isinstance(part, Element) else part for part in self.children).strip()


class Document(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element('document')
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, {key: value or '' for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def first(node, tag=None, css_class=None):
    return next(node.walk(tag, css_class), None) if node else None


def text(node):
    return ' '.join(node.text().split()) if node else ''


def http_url(value):
    try:
        url = urljoin(ORIGIN, value or '')
        return url if value and urlsplit(url).scheme in ('http', 'https') else ''
    except ValueError:
        return ''


def identity(link):
    if link is None:
        return None
    url = http_url(link.attrs.get('href'))
    parsed = urlsplit(url)
    match = re.fullmatch(r'/(?:book|info)/([0-9]+)/?', parsed.path)
    title = text(link)
    if not match or not title or parsed.hostname not in ('www.qidian.com', 'book.qidian.com'):
        return None
    return {'bookId': match[1], 'bookName': title, 'bookUrl': f'{ORIGIN}book/{match[1]}/'}


def word_count(value):
    match = WORD_COUNT.search(value)
    return ''.join(match.groups()) + '字' if match else ''


def category_tags(node):
    tags = []
    for link in node.walk('a') if node else []:
        path = urlsplit(http_url(link.attrs.get('href'))).path
        label = text(link).strip('「」')
        if path.startswith(('/all/', '/chan/')) and label and label not in tags:
            tags.append(label)
    return tags


def parse_rank(html):
    root = Document(html).root
    books = []
    seen = set()
    for block in root.walk(css_class='book-img-text'):
        for item in block.walk('li'):
            heading = first(item, 'h2') or first(item, 'h4')
            row = identity(first(heading, 'a'))
            if row is None or row['bookId'] in seen:
                continue
            seen.add(row['bookId'])
            meta = first(item, css_class='author')
            author = first(meta, 'a', 'name') or first(meta, 'a')
            update = text(first(item, css_class='update'))
            status_text = text(meta) + ' ' + update
            picture = first(item, 'img')
            cover = ''
            if picture:
                cover = http_url(picture.attrs.get('data-src') or picture.attrs.get('src'))
            row.update(author=text(author), tags=category_tags(meta),
                       intro=text(first(item, css_class='intro')), thumbUrl=cover,
                       wordCount=word_count(update),
                       status='完结' if re.search(r'完[结本]', status_text) else '')
            books.append(row)
    return books


def parse_detail(html):
    root = Document(html).root
    info = first(root, css_class='book-info')
    counts = first(root, css_class='count')
    author = first(first(root, css_class='book-meta'), css_class='author')
    # Do not interpret numbers in a novel's synopsis as its word-count metric.
    metric = word_count(text(counts))
    if not metric and info:
        metric = next((word_count(text(em)) for em in info.walk('em') if word_count(text(em))), '')
    return {'wc': metric,
            'author': re.sub(r'^作者\s*[:：]?\s*', '', text(author)),
            'intro': text(first(info, css_class='intro'))}


def parse_sanjiang(html):
    root = Document(html).root
    period = first(first(root, css_class='strongrec-wrap'), 'li', 'strongrec-list')
    listing = first(period, css_class='book-list')
    books = []
    seen = set()
    for item in listing.walk('li') if listing else []:
        row = identity(first(first(item, 'h2'), 'a', 'name'))
        if row is None or row['bookId'] in seen:
            continue
        seen.add(row['bookId'])
        recommendation = first(item, css_class='rec')
        row.update(tags=category_tags(item), rec=(recommendation.attrs.get('title') or text(recommendation))
                   if recommendation else '')
        books.append(row)
    return books


def to_book(raw, rank):
    """Keep the existing snapshot schema, with no genre-based gender inference."""
    mapping = {'title': 'bookName', 'author': 'author', 'reads': 'wordCount',
               'word_count': 'wordCount', 'word_count_observed_at': '_word_count_observed_at',
               'word_count_source': '_word_count_source', 'cover': 'thumbUrl', '_rec': 'rec'}
    result = {target: raw.get(source) or '' for target, source in mapping.items()}
    tags = raw.get('tags')
    result.update(metric_type='word_count', intro=raw.get('intro') or raw.get('rec') or '暂无简介',
                  url=raw.get('bookUrl') or f'{ORIGIN}book/{raw.get("bookId", "")}/',
                  _tags=list(dict.fromkeys(t for t in tags if isinstance(t, str))) if isinstance(tags, list) else [],
                  _gender='未知', _status=raw.get('status') or '未知', _rank=rank)
    return result
