@echo off
setlocal enabledelayedexpansion
title TSShara CLI - Gerenciador de Servico Windows

:: ============================================================================
:: TSShara CLI - Windows Service Installer
:: Uses NSSM (Non-Sucking Service Manager) to run as a Windows Service.
:: Download NSSM from: https://nssm.cc/download
:: Place nssm.exe in the same folder as this script, or add to PATH.
:: ============================================================================

set SERVICE_NAME=TSShara-Monitor
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
set SCRIPT_PATH=%SCRIPT_DIR%\tsshara-cli.py
set CONFIG_PATH=%SCRIPT_DIR%\config.ini

:: --- Check admin rights ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  [ERRO] Este script requer privilegios de administrador.
    echo         Clique com botao direito e selecione "Executar como administrador".
    echo.
    pause
    exit /b 1
)

:: --- Find Python ---
set PYTHON_PATH=
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYTHON_PATH set "PYTHON_PATH=%%i"
)
if not defined PYTHON_PATH (
    for /f "delims=" %%i in ('where python3 2^>nul') do (
        if not defined PYTHON_PATH set "PYTHON_PATH=%%i"
    )
)
if not defined PYTHON_PATH (
    echo.
    echo  [ERRO] Python nao encontrado no PATH.
    echo         Instale Python e adicione ao PATH do sistema.
    echo.
    pause
    exit /b 1
)

:: --- Find NSSM ---
set NSSM_PATH=
where nssm >nul 2>&1
if %errorLevel%==0 (
    for /f "delims=" %%i in ('where nssm') do (
        if not defined NSSM_PATH set "NSSM_PATH=%%i"
    )
) else (
    if exist "%SCRIPT_DIR%\nssm.exe" (
        set "NSSM_PATH=%SCRIPT_DIR%\nssm.exe"
    )
)

:menu
cls
echo.
echo  ============================================================
echo   TSShara CLI - Gerenciador de Servico Windows
echo  ============================================================
echo.
echo   Python:  %PYTHON_PATH%
echo   Script:  %SCRIPT_PATH%
echo   Config:  %CONFIG_PATH%
if defined NSSM_PATH (
    echo   NSSM:    %NSSM_PATH%
) else (
    echo   NSSM:    [NAO ENCONTRADO]
)
echo   Servico: %SERVICE_NAME%
echo.
echo  ------------------------------------------------------------
echo   1. Instalar dependencias Python
echo   2. Gerar config.ini padrao
echo   3. Instalar servico Windows
echo   4. Iniciar servico
echo   5. Parar servico
echo   6. Remover servico
echo   7. Verificar status do servico
echo   8. Sair
echo  ------------------------------------------------------------
echo.
choice /c 12345678 /n /m "  Escolha uma opcao: "

if %errorlevel%==1 goto deps
if %errorlevel%==2 goto initconfig
if %errorlevel%==3 goto install
if %errorlevel%==4 goto start
if %errorlevel%==5 goto stop
if %errorlevel%==6 goto remove
if %errorlevel%==7 goto status
if %errorlevel%==8 goto quit

:: --- Install Python dependencies ---
:deps
echo.
echo  Instalando dependencias Python...
echo.
"%PYTHON_PATH%" -m pip install pyserial colorama
echo.
echo  Dependencias instaladas.
echo.
pause
goto menu

:: --- Generate default config ---
:initconfig
echo.
echo  Gerando config.ini padrao...
echo.
"%PYTHON_PATH%" "%SCRIPT_PATH%" init-config --force
echo.
echo  Edite o arquivo config.ini conforme necessario antes de
echo  instalar o servico (principalmente configuracoes de email,
echo  porta serial e API).
echo.
pause
goto menu

:: --- Install service ---
:install
if not defined NSSM_PATH (
    echo.
    echo  [ERRO] NSSM nao encontrado.
    echo.
    echo  Baixe NSSM de: https://nssm.cc/download
    echo  Extraia nssm.exe para: %SCRIPT_DIR%
    echo  Ou adicione ao PATH do sistema.
    echo.
    pause
    goto menu
)

echo.
echo  Instalando servico %SERVICE_NAME%...
echo.

:: Remove existing service if any
"%NSSM_PATH%" stop %SERVICE_NAME% >nul 2>&1
"%NSSM_PATH%" remove %SERVICE_NAME% confirm >nul 2>&1

:: Install the service
"%NSSM_PATH%" install %SERVICE_NAME% "%PYTHON_PATH%" "\"%SCRIPT_PATH%\" monitor --config \"%CONFIG_PATH%\""
if %errorlevel% neq 0 (
    echo  [ERRO] Falha ao instalar servico.
    pause
    goto menu
)

:: Configure service properties
"%NSSM_PATH%" set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%"
"%NSSM_PATH%" set %SERVICE_NAME% DisplayName "TSShara Nobreak Monitor"
"%NSSM_PATH%" set %SERVICE_NAME% Description "Monitora nobreak TS Shara via serial e envia notificacoes. API REST para apps externos."
"%NSSM_PATH%" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%NSSM_PATH%" set %SERVICE_NAME% ObjectName LocalSystem
if not exist "%SCRIPT_DIR%\log" mkdir "%SCRIPT_DIR%\log"
"%NSSM_PATH%" set %SERVICE_NAME% AppStdout "%SCRIPT_DIR%\log\service-stdout.log"
"%NSSM_PATH%" set %SERVICE_NAME% AppStderr "%SCRIPT_DIR%\log\service-stderr.log"
"%NSSM_PATH%" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%NSSM_PATH%" set %SERVICE_NAME% AppStderrCreationDisposition 4
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateBytes 5242880
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateOnline 1

echo.
echo  Servico %SERVICE_NAME% instalado com sucesso!
echo  Use a opcao 4 para iniciar o servico.
echo.
echo  IMPORTANTE: Verifique as configuracoes em config.ini antes de iniciar.
echo.
pause
goto menu

:: --- Start service ---
:start
echo.
echo  Iniciando servico %SERVICE_NAME%...
echo.
net start %SERVICE_NAME%
echo.
pause
goto menu

:: --- Stop service ---
:stop
echo.
echo  Parando servico %SERVICE_NAME%...
echo.
net stop %SERVICE_NAME%
echo.
pause
goto menu

:: --- Remove service ---
:remove
if not defined NSSM_PATH (
    echo.
    echo  [ERRO] NSSM nao encontrado. Nao e possivel remover o servico.
    echo.
    pause
    goto menu
)

echo.
echo  Parando e removendo servico %SERVICE_NAME%...
echo.
net stop %SERVICE_NAME% 2>nul
"%NSSM_PATH%" remove %SERVICE_NAME% confirm
echo.
echo  Servico removido.
echo.
pause
goto menu

:: --- Service status ---
:status
echo.
sc query %SERVICE_NAME%
echo.
pause
goto menu

:: --- Exit ---
:quit
endlocal
exit /b 0
