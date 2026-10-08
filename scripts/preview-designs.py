"""Serve design concepts and the real login/license using isolated local storage."""
from pathlib import Path
from flask import send_from_directory, redirect
from onpf.app import create_app

ROOT = Path(__file__).resolve().parents[1]
app = create_app({'INSTANCE_PATH': ROOT / 'output/design-round-1/instance'})


@app.get('/designs/')
def designs_index():
    return redirect('/designs/index.html')


@app.get('/designs/<path:name>')
def design_file(name):
    return send_from_directory(ROOT / 'docs/design-round-1', name)


if __name__ == '__main__':
    from waitress import serve
    serve(app, host='127.0.0.1', port=8879)
