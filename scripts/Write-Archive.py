"""Archive the portable package without PowerShell 5.1 native -c quote loss."""
from pathlib import Path
import sys
import zipfile


def main():
    root, output = (Path(value).resolve() for value in sys.argv[1:])
    if output.is_relative_to(root):
        raise ValueError('The archive must be outside the package directory')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path, str(Path(root.name) / path.relative_to(root)))


if __name__ == '__main__':
    main()
