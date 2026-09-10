@echo off
chcp 936 >nul 2>&1
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
setlocal EnableDelayedExpansion

:: 颜色定义
set "GREEN=[92m"
set "RED=[91m"
set "YELLOW=[93m"
set "BLUE=[94m"
set "CYAN=[96m"
set "RESET=[0m"

:: 初始化日志
if not exist "logs" mkdir "logs"
for /f %%I in ('powershell -NoProfile -NonInteractive -Command "Get-Date -Format yyyyMMddHHmmss"') do set "dt=%%I"
set "LOG_DATE=%dt:~0,4%-%dt:~4,2%-%dt:~6,2%"
set "LOG_TIME=%dt:~8,2%:%dt:~10,2%:%dt:~12,2%"
set "LOG_FILE=logs\%LOG_DATE%_run.log"
set "ERROR_FILE=logs\%LOG_DATE%_error.log"

:: 写入日志头
echo ======================================== >> "%LOG_FILE%"
echo [%LOG_TIME%] 番茄指数启动 >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

title 小说指数 NovelIndex - 一键启动

if /i "%~1"=="/auto" goto AUTO_RUN

:MAIN_MENU
cls
echo.
echo  %CYAN%=============================================%RESET%
echo  %CYAN%       小说指数 NovelIndex - 一键启动%RESET%
echo  %CYAN%=============================================%RESET%
echo.
echo     [1] 首次安装（安装依赖 + 配置环境）
echo     [2] 运行爬虫（抓取今日数据）
echo     [3] 构建看板（生成趋势分析）
echo     [4] 启动预览（本地打开看板）
echo     [5] 完整流程（爬虫 + 构建 + 预览）
echo     [6] 配置 AI 服务
echo     [7] 定时任务设置
echo     [8] 数据清理
echo     [0] 退出
echo.
echo  %CYAN%=============================================%RESET%
echo.

set /p choice="请选择操作 [0-8]: "

if "%choice%"=="1" goto INSTALL
if "%choice%"=="2" goto SCRAPE
if "%choice%"=="3" goto BUILD
if "%choice%"=="4" goto PREVIEW
if "%choice%"=="5" goto FULL_RUN
if "%choice%"=="6" goto CONFIG_AI
if "%choice%"=="7" goto SCHEDULE_TASK
if "%choice%"=="8" goto CLEAN_DATA
if "%choice%"=="0" goto EXIT
echo  %RED%无效选择，请重新输入。%RESET%
call :LOG_ERROR "无效菜单选择"
timeout /t 2 >nul
goto MAIN_MENU

:AUTO_RUN
setlocal DisableDelayedExpansion
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\run_scheduled.ps1"
exit /b %ERRORLEVEL%

:INSTALL
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  首次安装%RESET%
echo  %CYAN%========================================%RESET%
echo.

call :LOG "开始首次安装"

echo.
echo   [■■■■□□□□□□□□□□□□□□□□] 25%% %BLUE%检查 Python 环境...%RESET%
echo.

call :LOG "检查 Python 环境"
python --version >nul 2>&1
if errorlevel 1 (
    echo  %RED%[错误] 未检测到 Python！%RESET%
    echo  请先安装 Python 3.10 或更高版本
    echo  https://www.python.org/downloads/
    call :LOG_ERROR "未检测到 Python"
    pause
    goto MAIN_MENU
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo  [ERROR] Python 3.10 or newer is required.
    pause
    goto MAIN_MENU
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  %GREEN%[OK]%RESET% Python 版本：%PYVER%
call :LOG "Python 版本: %PYVER%"
timeout /t 1 >nul

echo.
echo   [■■■■■■■■■□□□□□□□□□□□] 50%% %BLUE%安装依赖...%RESET%
echo.

call :LOG "开始安装依赖"
echo.
echo  选择 pip 安装源：
echo  %GREEN%[1]%RESET% 默认（PyPI 官方）
echo  %GREEN%[2]%RESET% 清华镜像（国内推荐）
echo  %GREEN%[3]%RESET% 阿里云镜像
echo  %RED%[N]%RESET% 跳过依赖安装
echo.
set /p "mirror=请选择 [1/2/3/N]: "
set "PIP_INDEX="
if "%mirror%"=="2" set "PIP_INDEX=-i https://pypi.tuna.tsinghua.edu.cn/simple"
if "%mirror%"=="3" set "PIP_INDEX=-i https://mirrors.aliyun.com/pypi/simple/"
if /i "%mirror%"=="N" (
    echo  %YELLOW%已跳过依赖安装%RESET%
    call :LOG "跳过依赖安装"
    timeout /t 1 >nul
    goto INSTALL_AFTER_DEP
)
python -m pip install -r requirements.txt -q %PIP_INDEX%
if errorlevel 1 (
    echo  %RED%[错误]安装依赖失败！%RESET%
    echo  若国内网络超时，建议选 [2] 清华镜像重试。
    call :LOG_ERROR "依赖安装失败"
    pause
    goto MAIN_MENU
)
echo  %GREEN%[OK]%RESET% 依赖安装完成
call :LOG "依赖安装完成"
:INSTALL_AFTER_DEP
python -m playwright install chromium
if errorlevel 1 (
    echo  [ERROR] Playwright browser installation failed.
    call :LOG_ERROR "Playwright browser installation failed"
    pause
    goto MAIN_MENU
)
python -m patchright install chromium
if errorlevel 1 (
    echo  [ERROR] Patchright browser installation failed.
    call :LOG_ERROR "Patchright browser installation failed"
    pause
    goto MAIN_MENU
)
echo  %GREEN%[OK]%RESET% 浏览器安装完成
call :LOG "浏览器安装完成"
timeout /t 1 >nul

echo.
echo   [■■■■■■■■■■■■■■■■■■■■] 100%% %GREEN%安装完成！%RESET%
call :LOG "首次安装完成"
echo.
echo  按任意键返回主菜单...
pause >nul
goto MAIN_MENU

:SCRAPE
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  运行爬虫%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo  将抓取番茄小说四大榜单数据
echo  预计耗时 10-15 分钟
echo.
echo  %GREEN%[Y]%RESET% 开始运行
echo  %RED%[N]%RESET% 返回主菜单
echo.
set /p confirm="请选择 [Y/N]: "
if /i not "%confirm%"=="Y" goto MAIN_MENU

call :LOG "开始运行爬虫"

echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  爬虫进度%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo   [□□□□□□□□□□□□□□□□□□□□] 0%% %BLUE%准备中...%RESET%
echo.
echo  %YELLOW%正在运行爬虫，请稍候...%RESET%
echo.

set "SCRAPE_START=%TIME%"
call :RUN_WITH_RETRY "python run_all.py --no-build" 3 10 "爬虫"
set "SCRAPE_END=%TIME%"

echo.
echo   [■■■■■■■■■■■■■■■■■■■■] 100%% %GREEN%完成！%RESET%
echo.
if errorlevel 1 (
    echo  %RED%[错误] 爬虫运行出错！%RESET%
    call :LOG_ERROR "爬虫运行失败"
) else (
    echo  %GREEN%[OK]%RESET% 爬虫运行完成！数据已保存到 data/ 目录。
    call :LOG "爬虫运行完成 (开始: %SCRAPE_START%, 结束: %SCRAPE_END%)"
)
echo.
echo  按任意键返回主菜单...
pause >nul
goto MAIN_MENU

:BUILD
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  构建看板数据%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo  %GREEN%[Y]%RESET% 开始构建
echo  %RED%[N]%RESET% 返回主菜单
echo.
set /p confirm="请选择 [Y/N]: "
if /i not "%confirm%"=="Y" goto MAIN_MENU

call :LOG "开始构建看板数据"

echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  构建进度%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo   [□□□□□□□□□□□□□□□□□□□□] 0%% %BLUE%准备中...%RESET%
echo.
echo  %YELLOW%正在分析数据，生成趋势...%RESET%
echo.

set "BUILD_START=%TIME%"
call :RUN_WITH_RETRY "python run_all.py --no-scrape" 3 10 "构建"
set "BUILD_END=%TIME%"

echo.
echo   [■■■■■■■■■■■■■■■■■■■■] 100%% %GREEN%完成！%RESET%
echo.
if errorlevel 1 (
    echo  %RED%[错误] 构建出错！%RESET%
    call :LOG_ERROR "构建失败"
) else (
    echo  %GREEN%[OK]%RESET% 构建完成！
    call :LOG "构建完成 (开始: %BUILD_START%, 结束: %BUILD_END%)"
)
echo.
echo  按任意键返回主菜单...
pause >nul
goto MAIN_MENU

:PREVIEW
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  启动预览%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo  %GREEN%[Y]%RESET% 启动服务器并打开浏览器
echo  %RED%[N]%RESET% 返回主菜单
echo.
set /p confirm="请选择 [Y/N]: "
if /i not "%confirm%"=="Y" goto MAIN_MENU

call :LOG "启动本地预览"

echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  启动进度%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo   [■■■■□□□□□□□□□□□□□□□□] 20%% %BLUE%启动服务器...%RESET%
start /b python scripts/serve_preview.py 8000
timeout /t 1 >nul

echo   [■■■■■■■■■□□□□□□□□□□□] 45%% %BLUE%等待服务器就绪...%RESET%
timeout /t 1 >nul

echo   [■■■■■■■■■■■■■■□□□□□□] 70%% %BLUE%打开浏览器...%RESET%
start "" "http://localhost:8000"
timeout /t 1 >nul

echo   [■■■■■■■■■■■■■■■■■■■■] 100%% %GREEN%启动完成！%RESET%
echo.
echo  看板地址：%GREEN%http://localhost:8000%RESET%
call :LOG "预览服务已启动"
echo  按任意键停止服务器并返回主菜单...
pause >nul
goto MAIN_MENU

:FULL_RUN
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  完整流程%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo  将依次执行：
echo    1. 运行爬虫（10-15 分钟）
echo    2. 构建看板数据
echo    3. 启动预览
echo.
echo  %GREEN%[Y]%RESET% 开始运行
echo  %RED%[N]%RESET% 返回主菜单
echo.
set /p confirm="请选择 [Y/N]: "
if /i not "%confirm%"=="Y" goto MAIN_MENU

call :LOG "开始完整流程"
set "FULL_START=%TIME%"

echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  整体进度%RESET%
echo  %CYAN%========================================%RESET%
echo.

echo   [■■■■□□□□□□□□□□□□□□□□] 10%% %BLUE%阶段 1/3：运行爬虫...%RESET%
echo   ----------------------------------------
call :LOG "阶段 1/3: 运行爬虫"
call :RUN_WITH_RETRY "python run_all.py --no-build" 3 10 "爬虫"
if errorlevel 1 (
    echo.
    echo   [■■■■□□□□□□□□□□□□□□□□] 10%% %RED%爬虫失败！%RESET%
    call :LOG_ERROR "完整流程中爬虫失败"
    echo  按任意键返回主菜单...
    pause >nul
    goto MAIN_MENU
)
echo.
echo   [■■■■■■■■■■■□□□□□□□□□] 50%% %GREEN%爬虫完成！%RESET%
call :LOG "爬虫阶段完成"
echo.

echo   [■■■■■■■■■■■■□□□□□□□□] 60%% %BLUE%阶段 2/3：构建看板...%RESET%
echo   ----------------------------------------
call :LOG "阶段 2/3: 构建看板"
call :RUN_WITH_RETRY "python run_all.py --no-scrape" 3 10 "构建"
if errorlevel 1 (
    echo.
    echo   [■■■■■■■■■■■■□□□□□□□□] 60%% %RED%构建失败！%RESET%
    call :LOG_ERROR "完整流程中构建失败"
    echo  按任意键返回主菜单...
    pause >nul
    goto MAIN_MENU
)
echo.
echo   [■■■■■■■■■■■■■■■■□□□□] 80%% %GREEN%构建完成！%RESET%
call :LOG "构建阶段完成"
echo.

echo   [■■■■■■■■■■■■■■■■■□□□] 90%% %BLUE%阶段 3/3：启动预览...%RESET%
echo   ----------------------------------------
call :LOG "阶段 3/3: 启动预览"
start /b python scripts/serve_preview.py 8000
timeout /t 2 >nul
start "" "http://localhost:8000"

set "FULL_END=%TIME%"
echo   [■■■■■■■■■■■■■■■■■■■■] 100%% %GREEN%全部完成！%RESET%
echo.
echo  看板地址：%GREEN%http://localhost:8000%RESET%
call :LOG "完整流程完成 (开始: %FULL_START%, 结束: %FULL_END%)"
echo  按任意键停止服务器并返回主菜单...
pause >nul
goto MAIN_MENU



:SCHEDULE_TASK
setlocal DisableDelayedExpansion
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage_tasks.ps1"
endlocal
goto MAIN_MENU

:CLEAN_DATA
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  数据清理%RESET%
echo  %CYAN%========================================%RESET%
echo.

call :LOG "开始数据清理"

:: 计算当前数据大小
set "TOTAL_SIZE=0"
for %%f in (data\*.json) do set /a "TOTAL_SIZE+=%%~zf"
set /a "TOTAL_MB=TOTAL_SIZE/1024/1024"

:: 统计文件数量
set "SNAPSHOT_COUNT=0"
for %%f in (data\fanqie_*_ranks_*.json) do set /a "SNAPSHOT_COUNT+=1"

set "TREND_COUNT=0"
for %%f in (data\trends\*.json) do set /a "TREND_COUNT+=1"

set "STATE_COUNT=0"
for %%f in (data\task_state_*.json) do set /a "STATE_COUNT+=1"

echo  %BLUE%当前数据占用空间: %TOTAL_MB% MB%RESET%
echo.
echo  数据文件统计:
echo    - 每日快照: %SNAPSHOT_COUNT% 个文件
echo    - 趋势归档: %TREND_COUNT% 个文件
echo    - 爬虫状态: %STATE_COUNT% 个文件
echo.
echo  %CYAN%----------------------------------------%RESET%
echo.
echo  请选择清理策略:
echo  %GREEN%[1]%RESET% 保留最近 7 天数据
echo  %GREEN%[2]%RESET% 保留最近 14 天数据
echo  %GREEN%[3]%RESET% 保留最近 30 天数据（推荐）
echo  %GREEN%[4]%RESET% 自定义保留天数
echo  %RED%[N]%RESET% 返回主菜单
echo.
set /p choice="请选择 [1-4/N]: "

if "%choice%"=="1" set "KEEP_DAYS=7"
if "%choice%"=="2" set "KEEP_DAYS=14"
if "%choice%"=="3" set "KEEP_DAYS=30"
if "%choice%"=="4" (
    set /p KEEP_DAYS="请输入保留天数: "
)
if /i "%choice%"=="N" goto MAIN_MENU

echo.
echo  %YELLOW%将删除 %KEEP_DAYS% 天前的数据文件%RESET%
echo.
echo  %RED%[Y]%RESET% 确认删除
echo  %GREEN%[N]%RESET% 取消
echo.
set /p confirm="确认删除？[Y/N]: "
if /i not "%confirm%"=="Y" goto MAIN_MENU

echo.
echo  %YELLOW%正在清理...%RESET%
echo.

:: 调用 Python 清理脚本
python scripts\cleanup_task_states.py --keep-days "!KEEP_DAYS!"
if errorlevel 1 (
    echo  %RED%[失败]%RESET% 清理未完成，请查看上方原因；未删除的文件可稍后重试。
    call :LOG_ERROR "数据清理未完成"
    pause >nul
    goto MAIN_MENU
)

:: 清理爬虫状态文件
:: (task_state cleanup handled by the script call above)

:: 计算清理后的大小
set "NEW_SIZE=0"
for %%f in (data\*.json) do set /a "NEW_SIZE+=%%~zf"
set /a "NEW_MB=NEW_SIZE/1024/1024"
set /a "FREED_MB=TOTAL_MB-NEW_MB"

echo.
echo  %GREEN%清理完成！%RESET%
echo    释放空间: %FREED_MB% MB
echo    剩余数据: %NEW_MB% MB
call :LOG "数据清理完成，释放 %FREED_MB% MB"
echo.
echo  按任意键返回主菜单...
pause >nul
goto MAIN_MENU

:: ============================================================
:: 配置 AI 服务（支持多 endpoint 故障转移）
:: 流程：显示现状 -> 供应商预设 -> 单/多endpoint -> 逐个填+连通性测试 -> 合并写 .env
:: ============================================================
:CONFIG_AI
cls
echo.
echo  %CYAN%========================================%RESET%
echo  %CYAN%  配置 AI 服务（支持多 endpoint 故障转移）%RESET%
echo  %CYAN%========================================%RESET%
echo.
echo  支持任意 OpenAI 兼容 API（DeepSeek/OpenAI/Moonshot/硅基流动等）。
echo  多 endpoint：同供应商多 key 分摊配额，不同供应商故障转移。
echo.

call :LOG "开始配置 AI 服务"

:: ---- 显示当前 .env 中的 endpoint 配置 ----
echo  %BLUE%[当前配置]%RESET%
if not exist ".env" (
    echo  尚未创建 .env 文件
) else (
    set "HAS_API=0"
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        set "KL=%%a"
        set "VL=%%b"
        setlocal enabledelayedexpansion
        for /f "tokens=* delims= " %%x in ("!KL!") do set "KL=%%x"
        echo !KL! | findstr /b /i "API_" >nul
        if !errorlevel! equ 0 (
            echo  !KL!=!VL!
            endlocal & set "HAS_API=1"
        ) else (
            endlocal
        )
    )
    if "!HAS_API!"=="0" echo  （未发现已配置的 API_* endpoint）
)
echo.

:: ---- 供应商预设 ----
echo  %BLUE%[选择供应商预设]%RESET%（预设只预填 base_url 和 model，key 仍需手动输入）
echo  %GREEN%[1]%RESET% DeepSeek    base=https://api.deepseek.com/v1   model=deepseek-chat
echo  %GREEN%[2]%RESET% OpenAI      base=https://api.openai.com/v1     model=gpt-4o-mini
echo  %GREEN%[3]%RESET% Moonshot    base=https://api.moonshot.cn/v1    model=moonshot-v1-8k
echo  %GREEN%[4]%RESET% 硅基流动    base=https://api.siliconflow.cn/v1 model=Qwen/Qwen2.5-7B-Instruct
echo  %GREEN%[5]%RESET% 自定义（全部手填）
echo  %GREEN%[0]%RESET% 跳过预设，直接逐个填
echo.
set /p "preset=请选择 [0-5]: "

set "DEF_BASE="
set "DEF_MODEL="
if "%preset%"=="1" ( set "DEF_BASE=https://api.deepseek.com/v1"   & set "DEF_MODEL=deepseek-chat" )
if "%preset%"=="2" ( set "DEF_BASE=https://api.openai.com/v1"     & set "DEF_MODEL=gpt-4o-mini" )
if "%preset%"=="3" ( set "DEF_BASE=https://api.moonshot.cn/v1"    & set "DEF_MODEL=moonshot-v1-8k" )
if "%preset%"=="4" ( set "DEF_BASE=https://api.siliconflow.cn/v1" & set "DEF_MODEL=Qwen/Qwen2.5-7B-Instruct" )

:: ---- 单 / 多 endpoint ----
echo.
echo  %BLUE%[endpoint 模式]%RESET%
echo  %GREEN%[1]%RESET% 单 endpoint（仅配一组，向后兼容旧 API_BASE_URL）
echo  %GREEN%[2]%RESET% 多 endpoint 故障转移（逐个填 API_1_/API_2_…，最多 9 组）
echo.
set /p "mode=请选择 [1/2]: "
if "%mode%"=="2" ( set "USE_MULTI=1" ) else ( set "USE_MULTI=0" )

:: 清空编号变量 EP1..EP9
for /l %%n in (1,1,9) do ( set "EP%%n_BASE=" & set "EP%%n_KEY=" & set "EP%%n_MODEL=" )
set "EP_COUNT=0"

:EP_LOOP
set /a "EP_COUNT+=1"
echo.
echo  %CYAN%-------- endpoint %EP_COUNT% --------%RESET%
if defined DEF_BASE (
    echo  base_url 预设: %GREEN%%DEF_BASE%%RESET%（回车采用，或输入新值）
) else (
    echo  请输入 base_url（如 https://api.deepseek.com/v1）
)
:GET_BASE
set "IN_BASE="
set /p "IN_BASE=  base_url: "
if not defined IN_BASE set "IN_BASE=%DEF_BASE%"
call :CHECK_SPECIAL "!IN_BASE!"
if errorlevel 1 (
    echo  [ERROR] input contains forbidden special characters, please re-enter.
    goto GET_BASE
)
if not defined IN_BASE (
    echo  %RED%[警告] base_url 为空，该 endpoint 跳过%RESET%
    set /a "EP_COUNT-=1"
    goto EP_NEXT
)

if defined DEF_MODEL (
    echo  model 预设: %GREEN%%DEF_MODEL%%RESET%（回车采用，或输入新值）
) else (
    echo  请输入 model（如 deepseek-chat）
)
:GET_MODEL
set "IN_MODEL="
set /p "IN_MODEL=  model: "
if not defined IN_MODEL set "IN_MODEL=%DEF_MODEL%"
call :CHECK_SPECIAL "!IN_MODEL!"
if errorlevel 1 (
    echo  [ERROR] input contains forbidden special characters, please re-enter.
    goto GET_MODEL
)

:GET_KEY
set "IN_KEY="
set /p "IN_KEY=  api_key: "
if not defined IN_KEY (
    echo  %RED%[警告] 未输入 key，该 endpoint 跳过%RESET%
    set /a "EP_COUNT-=1"
    goto EP_NEXT
)

echo  %BLUE%测试连通性...%RESET%
call :CHECK_SPECIAL "!IN_KEY!"
if errorlevel 1 (
    echo  [ERROR] input contains forbidden special characters, please re-enter.
    goto GET_KEY
)
call :TEST_ENDPOINT "%IN_BASE%" "%IN_KEY%" "%IN_MODEL%"

:: 存入编号变量
set "EP%EP_COUNT%_BASE=%IN_BASE%"
set "EP%EP_COUNT%_KEY=%IN_KEY%"
set "EP%EP_COUNT%_MODEL=%IN_MODEL%"
echo  %GREEN%[已记录]%RESET% endpoint %EP_COUNT%

:EP_NEXT
if "%USE_MULTI%"=="1" (
    if %EP_COUNT% lss 9 (
        echo.
        set /p "more=继续添加下一个 endpoint？[Y/N]: "
        if /i "!more!"=="Y" goto EP_LOOP
    ) else (
        echo  %YELLOW%已达上限 9 个 endpoint%RESET%
    )
)

if %EP_COUNT% leq 0 (
    echo.
    echo  %RED%[错误] 未配置任何有效 endpoint，已取消。%RESET%
    call :LOG_ERROR "AI 配置取消：无有效 endpoint"
    pause >nul
    goto MAIN_MENU
)

:: ---- 多 endpoint 时的故障转移开关 ----
set "PRIMARY_ONLY="
if "%USE_MULTI%"=="1" (
    echo.
    echo  %BLUE%[故障转移模式]%RESET%
    echo  %GREEN%[1]%RESET% round-robin（默认，多 endpoint 均匀分摊调用）
    echo  %GREEN%[2]%RESET% 主从模式（始终优先 endpoint 1，仅当其失败/冷却才切备份）
    echo.
    set /p "pmode=请选择 [1/2]: "
    if "!pmode!"=="2" set "PRIMARY_ONLY=1"
)

echo.
echo  %BLUE%[写入 .env]%RESET%
call :WRITE_ENV "%USE_MULTI%" "%PRIMARY_ONLY%" %EP_COUNT%

echo.
echo  %GREEN%[OK]%RESET% AI 配置已保存到 .env
echo  运行 %GREEN%[3] 构建看板%RESET% 可验证 AI 是否生效（输出含"AI 总结已生成"）。
call :LOG "AI 配置已保存（endpoint 数=%EP_COUNT%, 多endpoint=%USE_MULTI%）"
echo.
echo  按任意键返回主菜单...
pause >nul
goto MAIN_MENU

:EXIT
call :LOG "用户退出"
echo ======================================== >> "%LOG_FILE%"
echo [%TIME%] 番茄指数退出 >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"
echo.
echo  %CYAN%感谢使用番茄指数！再见！%RESET%
echo.
exit /b 0


:: 重试函数
:RUN_WITH_RETRY
:: 参数: %1=命令 %2=最大重试次数 %3=重试间隔(秒) %4=任务名称
set "RETRY_COMMAND=%~1"
set "MAX_RETRY=%~2"
set "RETRY_DELAY=%~3"
set "TASK_NAME=%~4"
set "RETRY_COUNT=0"

:RETRY_LOOP
set /a "RETRY_COUNT+=1"
echo  %YELLOW%第 %RETRY_COUNT% 次尝试...%RESET%
call :LOG "%TASK_NAME% - 第 %RETRY_COUNT% 次尝试"

%RETRY_COMMAND%

if errorlevel 1 (
    if %RETRY_COUNT% lss %MAX_RETRY% (
        echo  %YELLOW%[警告]%RESET% 失败，%RETRY_DELAY%秒后重试...
        call :LOG_ERROR "%TASK_NAME% - 第 %RETRY_COUNT% 次失败，%RETRY_DELAY%秒后重试"
        timeout /t %RETRY_DELAY% >nul
        goto RETRY_LOOP
    ) else (
        echo  %RED%[错误]%RESET% 已重试 %MAX_RETRY% 次，仍然失败！
        call :LOG_ERROR "%TASK_NAME% - 已重试 %MAX_RETRY% 次，仍然失败"
        exit /b 1
    )
)

echo  %GREEN%[OK]%RESET% %TASK_NAME% 执行成功！
call :LOG "%TASK_NAME% - 第 %RETRY_COUNT% 次尝试成功"
exit /b 0

:: 日志函数
:LOG
:: 参数: %1=消息
set "LOG_TIME_NOW=%TIME:~0,8%"
set "LOG_TIME_NOW=%LOG_TIME_NOW: =0%"
echo [%LOG_TIME_NOW%] %~1 >> "%LOG_FILE%"
echo  %BLUE%[%LOG_TIME_NOW%]%RESET% %~1
goto :eof

:LOG_ERROR
:: 参数: %1=消息
set "LOG_TIME_NOW=%TIME:~0,8%"
set "LOG_TIME_NOW=%LOG_TIME_NOW: =0%"
echo [%LOG_TIME_NOW%] [错误] %~1 >> "%ERROR_FILE%"
echo [%LOG_TIME_NOW%] [错误] %~1 >> "%LOG_FILE%"
echo  %RED%[%LOG_TIME_NOW%] [错误]%RESET% %~1
goto :eof


:: ============================================================
:: 测试单个 endpoint 连通性（调用方传 base key model）
:: 用临时 .py 文件测试，退出码 0=成功/限流(可保存)  1=鉴权/连接失败
:: ============================================================
:CHECK_SPECIAL
:: N72: reject inputs containing chars that corrupt .env writes or bat syntax
:: DisableDelayedExpansion so ! in the raw value is not re-expanded before validation
setlocal DisableDelayedExpansion
python -c "import sys; s=sys.argv[1]; sys.exit(0 if all(c not in '!^&|<>%' for c in s) else 1)" "%~1"
set "CHK_CODE=%ERRORLEVEL%"
endlocal & exit /b %CHK_CODE%

:TEST_ENDPOINT
setlocal
set "T_BASE=%~1"
set "T_KEY=%~2"
set "T_MODEL=%~3"
set "TSCRIPT=%TEMP%\novelindex_aitest.py"
del "%TSCRIPT%" >nul 2>&1
set "AITEST_BASE=%T_BASE%"
set "AITEST_KEY=%T_KEY%"
set "AITEST_MODEL=%T_MODEL%"
> "%TSCRIPT%" echo import sys
>> "%TSCRIPT%" echo from openai import OpenAI,APIError,APIConnectionError,APITimeoutError,RateLimitError,AuthenticationError
>> "%TSCRIPT%" echo import os
>> "%TSCRIPT%" echo c=OpenAI(base_url=os.environ['AITEST_BASE'],api_key=os.environ['AITEST_KEY'],timeout=15)
>> "%TSCRIPT%" echo try:
>> "%TSCRIPT%" echo     c.chat.completions.create(model=os.environ['AITEST_MODEL'],messages=[{'role':'user','content':'hi'}],max_tokens=1)
>> "%TSCRIPT%" echo     print('  OK')
>> "%TSCRIPT%" echo     sys.exit(0)
>> "%TSCRIPT%" echo except AuthenticationError:
>> "%TSCRIPT%" echo     print('  AUTH');sys.exit(1)
>> "%TSCRIPT%" echo except RateLimitError:
>> "%TSCRIPT%" echo     print('  RATE');sys.exit(0)
>> "%TSCRIPT%" echo except APITimeoutError:
>> "%TSCRIPT%" echo     print('  TIMEOUT');sys.exit(1)
>> "%TSCRIPT%" echo except APIConnectionError:
>> "%TSCRIPT%" echo     print('  CONN');sys.exit(1)
>> "%TSCRIPT%" echo except APIError as e:
>> "%TSCRIPT%" echo     print('  APIERR:'+str(e)[:120]);sys.exit(1)
>> "%TSCRIPT%" echo except Exception as e:
>> "%TSCRIPT%" echo     print('  ERR:'+str(e)[:120]);sys.exit(1)
for /f "usebackq delims=" %%r in (`python "%TSCRIPT%"`) do set "TRESULT=%%r"
del "%TSCRIPT%" >nul 2>&1

if "%TRESULT%"=="OK" (
    echo  %GREEN%[OK]%RESET% 连通正常
    call :LOG "endpoint 测试通过: %~1 / %~3"
    endlocal & exit /b 0
)
if "%TRESULT%"=="RATE" (
    echo  %YELLOW%[受限]%RESET% 429 配额/限流：key 可用但超额（仍可保存）
    call :LOG "endpoint 限流但仍可用: %~1 / %~3"
    endlocal & exit /b 0
)
if "%TRESULT%"=="AUTH" (
    echo  %RED%[失败]%RESET% 鉴权失败(401/403)：key 或 base_url 错误
)
if "%TRESULT%"=="TIMEOUT" (
    echo  %YELLOW%[超时]%RESET% 请求超时：网络慢或 base_url 不通
)
if "%TRESULT%"=="CONN" (
    echo  %RED%[失败]%RESET% 连接失败：base_url 不通或需代理
)
if "%TRESULT%"=="APIERR" (
    echo  %RED%[失败]%RESET% API 错误（model 可能不存在或参数有误）
)
if "%TRESULT:~0,3%"=="ERR" (
    echo  %RED%[失败]%RESET% 异常：%TRESULT:~4%
)
call :LOG_ERROR "endpoint 测试失败: %~1 / %~3 (%TRESULT%)"
endlocal & exit /b 1

:: ============================================================
:: 合并写入 .env：保留非 endpoint 行，剔除旧 API_*，追加新 endpoint
:: %1=USE_MULTI  %2=PRIMARY_ONLY(可空)  %3=EP_COUNT
:: 用编号变量 EP{n}_BASE/KEY/MODEL（已在调用方 setlocal 主域设置，
:: 但本子过程 setlocal 隔离了——故改由调用方在主域延迟展开写入）
:: ============================================================
:WRITE_ENV
setlocal enabledelayedexpansion
set "W_MULTI=%~1"
set "W_PRIMARY=%~2"
set "W_CNT=%~3"
set "ENV_FILE=.env"
set "TMP_FILE=.env.tmp_novelindex"

:: 1) 读现有 .env，保留非 API_ 开头的行
if exist "%ENV_FILE%" (
    type nul > "%TMP_FILE%"
    for /f "usebackq eol=# tokens=*" %%a in ("%ENV_FILE%") do (
        set "LINE=%%a"
        set "SKIP=0"
        echo !LINE! | findstr /b /i "API_ AI_PRIMARY_ONLY" >nul && set "SKIP=1"
        if "!SKIP!"=="0" >> "%TMP_FILE%" echo !LINE!
    )
) else (
    type nul > "%TMP_FILE%"
)

>> "%TMP_FILE%" echo # AI endpoints (generated by setup.bat)

:: 2) 追加新 endpoint（从主域编号变量 EP{n}_* 读取——但已 setlocal 隔离，
::    改用调用方传入：此处通过 delayed expansion 访问外层 EP{n}_* 不可见。
::    解决：WRITE_ENV 不 setlocal，直接在调用方主域执行写入逻辑。
::    这里 endlocal 后由调用方继续——但为简化，改为：本过程接收参数时，
::    调用方已 setlocal enabledelayedexpansion，故 !EP{n}_BASE! 可见。）
if "!W_MULTI!"=="1" (
    for /l %%n in (1,1,!W_CNT!) do (
        >> "%TMP_FILE%" echo API_%%n_BASE_URL=!EP%%n_BASE!
        >> "%TMP_FILE%" echo API_%%n_KEY=!EP%%n_KEY!
        >> "%TMP_FILE%" echo API_%%n_MODEL=!EP%%n_MODEL!
    )
    if defined W_PRIMARY (
        >> "%TMP_FILE%" echo # primary-only: prefer API_1, fallback on failure/cooldown
        >> "%TMP_FILE%" echo AI_PRIMARY_ONLY=1
    ) else (
        >> "%TMP_FILE%" echo # round-robin: distribute across endpoints
    )
) else (
    >> "%TMP_FILE%" echo API_BASE_URL=!EP1_BASE!
    >> "%TMP_FILE%" echo API_KEY=!EP1_KEY!
    >> "%TMP_FILE%" echo API_MODEL=!EP1_MODEL!
)

move /y "%TMP_FILE%" "%ENV_FILE%" >nul
endlocal
exit /b 0
