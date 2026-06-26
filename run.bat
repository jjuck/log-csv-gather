@echo off
setlocal

set "APP_DIR=%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
set "PYDANTIC_DISABLE_PLUGINS=1"

if exist "%APP_DIR%python\python.exe" (
  set "PYTHON_EXE=%APP_DIR%python\python.exe"
) else if exist "%APP_DIR%.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%APP_DIR%.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

set "COMMAND=%~1"
if "%COMMAND%"=="" set "COMMAND=web"

set "VALID_COMMAND="
for %%C in (web auth upload-once download-once doctor status) do (
  if /I "%COMMAND%"=="%%C" set "VALID_COMMAND=1"
)
if "%VALID_COMMAND%"=="" goto usage

set "CONFIG=%~2"
if "%CONFIG%"=="" call :resolve_config
if "%CONFIG%"=="" exit /b 1

if /I "%COMMAND%"=="web" (
  "%PYTHON_EXE%" -m log_csv_gather web --config "%CONFIG%"
  exit /b %ERRORLEVEL%
)

if /I "%COMMAND%"=="upload-once" (
  "%PYTHON_EXE%" -m log_csv_gather upload --config "%CONFIG%"
  exit /b %ERRORLEVEL%
)

if /I "%COMMAND%"=="auth" (
  "%PYTHON_EXE%" -m log_csv_gather auth --config "%CONFIG%"
  exit /b %ERRORLEVEL%
)

if /I "%COMMAND%"=="download-once" (
  "%PYTHON_EXE%" -m log_csv_gather download --config "%CONFIG%"
  exit /b %ERRORLEVEL%
)

if /I "%COMMAND%"=="doctor" (
  "%PYTHON_EXE%" -m log_csv_gather doctor --config "%CONFIG%"
  exit /b %ERRORLEVEL%
)

if /I "%COMMAND%"=="status" (
  "%PYTHON_EXE%" -m log_csv_gather status --config "%CONFIG%" --details
  exit /b %ERRORLEVEL%
)

:usage
echo Unknown command: %COMMAND%
echo Usage:
echo   run.bat [web^|auth^|upload-once^|download-once^|doctor^|status] [config-path]
exit /b 1

:resolve_config
set "ACTIVE_CONFIG=%APP_DIR%configs\active.yaml"
set "UPLOADER_CONFIG=%APP_DIR%configs\production.uploader.yaml"
set "DOWNLOADER_CONFIG=%APP_DIR%configs\production.downloader.yaml"

if exist "%ACTIVE_CONFIG%" (
  set "CONFIG=%ACTIVE_CONFIG%"
  goto :eof
)

if exist "%UPLOADER_CONFIG%" if not exist "%DOWNLOADER_CONFIG%" (
  set "CONFIG=%UPLOADER_CONFIG%"
  goto :eof
)

if exist "%DOWNLOADER_CONFIG%" if not exist "%UPLOADER_CONFIG%" (
  set "CONFIG=%DOWNLOADER_CONFIG%"
  goto :eof
)

if exist "%UPLOADER_CONFIG%" if exist "%DOWNLOADER_CONFIG%" (
  echo.
  echo PC role setup is required only once.
  echo   1. Field PC upload dashboard
  echo   2. Management PC download dashboard
  echo.
  choice /C 12 /N /M "Select this PC role [1/2]: "
  if errorlevel 2 (
    copy /Y "%DOWNLOADER_CONFIG%" "%ACTIVE_CONFIG%" >nul
  ) else (
    copy /Y "%UPLOADER_CONFIG%" "%ACTIVE_CONFIG%" >nul
  )
  if exist "%ACTIVE_CONFIG%" (
    set "CONFIG=%ACTIVE_CONFIG%"
    echo Saved selected config to "%ACTIVE_CONFIG%".
    goto :eof
  )
)

echo No config file was found.
echo Put one of these files under "%APP_DIR%configs":
echo   active.yaml
echo   production.uploader.yaml
echo   production.downloader.yaml
goto :eof
