@echo off
echo Building AI Production Studio...
pip install pyinstaller -q
pyinstaller AIProductionStudio.spec --clean
echo.
echo Build complete. Installer in dist/AIProductionStudio/
pause
