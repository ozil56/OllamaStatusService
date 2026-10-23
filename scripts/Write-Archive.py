"""Archive the portable package without PowerShell 5.1 native -c quote loss."""
from pathlib import Path
import sys
import zipfile


def main():
    root, output = (Path(value).resolve() for value in sys.argv[1:])
    if output.is_relative_to(root):
        raise ValueError('The archive must be outside the package directory')
    files = [path for path in sorted(root.rglob('*'))
             if path.is_file() and '__pycache__' not in path.parts]
    for path in files:
        if not path.relative_to(root).as_posix().isascii():
            raise ValueError('Distributed filenames must be ASCII: ' + str(path))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, str(Path(root.name) / path.relative_to(root)))


if __name__ == '__main__':
    main()
