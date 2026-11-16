@echo off
REM Leave this Remote Desktop session WITHOUT locking it, so CareerPilot can keep showing and driving the DeepSeek
REM sign-in windows (a locked screen swallows the real mouse and keyboard input they need).
REM Run as administrator from inside the Remote Desktop session: the session is handed to the console and your
REM Remote Desktop window closes. Everything keeps running. Reconnect with Remote Desktop whenever you like.
if /i "%SESSIONNAME%"=="Console" (echo This is already the console session - nothing to do. & pause & exit /b 0)
tscon "%SESSIONNAME%" /dest:console
if errorlevel 1 (echo tscon failed - right-click this file and choose "Run as administrator". & pause)
