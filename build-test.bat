@echo off
rmdir dist /s /q
pip uninstall -y ripple1d

python -m build_wheel

REM Get the name of the newly created wheel file in the dist directory
for %%f in (dist\*.whl) do (
    set WHEEL_FILE=%%f
)

pip install %WHEEL_FILE%

@REM ripple1d start --flask_debug
