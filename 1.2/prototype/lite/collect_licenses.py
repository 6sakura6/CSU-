"""Keep installed dependencies' license notices beside the portable prototype."""
from pathlib import Path
import importlib.metadata
import sys


def collect(root):
    target = Path(root) / 'licenses'
    target.mkdir(exist_ok=True)
    packages = ['pywebview', 'pythonnet', 'clr_loader', 'cffi', 'websocket-client',
                'bottle', 'proxy_tools', 'typing_extensions', 'pycparser', 'pyinstaller']
    for package in packages:
        distribution = importlib.metadata.distribution(package)
        for item in distribution.files or []:
            if (any(part.lower().startswith(('license', 'copying', 'copyright', 'notice'))
                    for part in item.parts) and item.suffix.lower() not in ('.py', '.pyc')):
                source = distribution.locate_file(item)
                if source.is_file():
                    (target / (package + '-' + source.name)).write_bytes(source.read_bytes())
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        (target / 'Python-LICENSE.txt').write_bytes(python_license.read_bytes())


if __name__ == '__main__':
    collect(sys.argv[1])
