#!/bin/bash
echo "Building AI Production Studio..."
pip install pyinstaller -q
pyinstaller AIProductionStudio.spec --clean
echo ""
echo "Build complete. Output in dist/AIProductionStudio/"
