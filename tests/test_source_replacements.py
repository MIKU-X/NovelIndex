import gzip
import io
import json
import unittest
import urllib.error
import zlib
from unittest.mock import Mock

from scrapers.jjwxc import extract_monthly_rank, parse_score
from scrapers.json_http import JsonHttpClient
from scrapers.qidian_html import parse_detail, parse_rank, parse_sanjiang, to_book


class QidianHtmlTests(unittest.TestCase):
    def test_list_identity_categories_lazy_cover_and_metric(self):
        html = '''<div class="book-img-text"><ul><li>
          <h2><a href="//book.qidian.com/info/123/">合成 &amp; 书名</a></h2>
          <p class="author"><a class="name" href="//my.qidian.com/author/1">作者甲</a>
            <a href="/all/chanId21/">玄幻</a><span>完本</span><a href="/vip/">VIP</a></p>
          <p class="intro">合成简介<script>untrusted()</script></p>
          <p class="update">99万推荐 · 12.5 万字</p>
          <img src="data:image/gif;base64,AA" data-src="//img.example.invalid/1.jpg">
        </li><li><h2><a href="https://evil.invalid/book/123/">外站</a></h2></li>
        <li><h2><a href="javascript:alert(1)">无书籍ID</a></h2></li></ul></div>'''
        rows = parse_rank(html)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['bookUrl'], 'https://www.qidian.com/book/123/')
        self.assertEqual(row['bookName'], '合成 & 书名')
        self.assertEqual(row['tags'], ['玄幻'])
        self.assertEqual(row['wordCount'], '12.5万字')
        self.assertEqual(row['thumbUrl'], 'https://img.example.invalid/1.jpg')
        self.assertEqual(row['intro'], '合成简介')
        book = to_book(row, 1)
        self.assertEqual(book['_gender'], '未知')
        self.assertEqual(book['_status'], '完结')

    def test_detail_requires_word_unit_and_ignores_synopsis_numbers(self):
        html = '<div class="book-info"><p class="intro">写满了100万字的信</p></div>'
        self.assertEqual(parse_detail(html)['wc'], '')
        html += '<p class="count"><em>12.25</em><span>万字</span><em>56</em>推荐</p>'
        html += '<div class="book-meta"><a class="author">作者： 甲</a></div>'
        self.assertEqual(parse_detail(html), {'wc': '12.25万字', 'author': '甲', 'intro': '写满了100万字的信'})

    def test_sanjiang_only_current_period(self):
        def period(book_id):
            return f'''<li class="strongrec-list"><div class="book-list"><ul><li>
              <h2><a class="name" href="/book/{book_id}/">合成{book_id}</a></h2>
              <a class="channel" href="/all/chanId1/">「奇幻」</a>
              <span class="rec" title="推荐全文">短文</span></li></ul></div></li>'''
        rows = parse_sanjiang('<ul class="strongrec-wrap">' + period(1) + period(2) + '</ul>')
        self.assertEqual([r['bookId'] for r in rows], ['1'])
        self.assertEqual(rows[0]['rec'], '推荐全文')


class Response(io.BytesIO):
    def __init__(self, raw, encoding=''):
        super().__init__(raw)
        self.headers = {'Content-Encoding': encoding}


class JsonTransportTests(unittest.TestCase):
    def test_compression_and_minimal_headers(self):
        raw = json.dumps({'text': '合成'}).encode()
        for encoding, encoded in (('gzip', gzip.compress(raw)), ('deflate', zlib.compress(raw)),
                                  ('deflate', zlib.compress(raw)[2:-4]), ('', raw)):
            opener = Mock(return_value=Response(encoded, encoding))
            client = JsonHttpClient(opener=opener)
            self.assertEqual(client.get('https://example.invalid/'), ({'text': '合成'}, None))
            request = opener.call_args.args[0]
            self.assertEqual(request.get_header('User-agent'), 'NovelIndex/1.0')
            self.assertIsNone(request.get_header('Origin'))

    def test_limits_apply_to_compressed_and_uncompressed_payload(self):
        for raw, encoding in ((b'x' * 101, ''), (gzip.compress(b'x' * 10000), 'gzip')):
            opener = Mock(side_effect=lambda *a, **k: Response(raw, encoding))
            client = JsonHttpClient(max_bytes=100, attempts=1, opener=opener)
            self.assertIn(client.get('https://example.invalid/')[1], ('size', 'parse'))

    def test_rate_limit_stops_immediately_and_retry_after_is_respected(self):
        for status in (403, 429, 503):
            clock = Mock(return_value=100)
            opener = Mock(side_effect=urllib.error.HTTPError('https://example.invalid/', status, 'blocked', {'Retry-After': '120'}, None))
            client = JsonHttpClient(opener=opener, clock=clock)
            self.assertEqual(client.get('https://example.invalid/'), (None, status))
            clock.return_value = 219
            self.assertEqual(client.get('https://example.invalid/'), (None, status))
            self.assertEqual(opener.call_count, 1)

    def test_definitive_errors_not_retried_but_network_errors_are_bounded(self):
        opener = Mock(side_effect=urllib.error.HTTPError('https://example.invalid/', 404, 'gone', {}, None))
        sleep = Mock()
        client = JsonHttpClient(opener=opener, sleep=sleep)
        self.assertEqual(client.get('https://example.invalid/'), (None, 404))
        self.assertEqual(opener.call_count, 1)
        opener.side_effect = OSError('offline')
        opener.reset_mock()
        self.assertEqual(client.get('https://example.invalid/'), (None, 'network'))
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_monthly_payload_skips_malformed_sections(self):
        value = {'data': [None, {'rank_group_type': 'rankings', 'data': [None, {'channelName': None},
            {'channelName': '月榜', 'list': [None, {'novelId': '123'}, {'novelId': 'invalid'}]}]}]}
        self.assertEqual(extract_monthly_rank(value), [{'novelId': '123'}])
        for metric in ('NaN', 'Infinity', '-10', '1万垃圾', None):
            self.assertEqual(parse_score(metric), 0)
