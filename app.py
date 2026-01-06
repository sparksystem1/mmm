import os
import tempfile
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import subprocess

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'flac'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'MMM API Ready'})

@app.route('/api/obliterate', methods=['POST'])
def obliterate():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400
        
        filename = secure_filename(file.filename)
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], f'input_{filename}')
        file.save(input_path)
        
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'output_{filename}')
        
        paranoid = request.form.get('paranoid', 'false') == 'true'
        verify = request.form.get('verify', 'false') == 'true'
        turbo = request.form.get('turbo', 'false') == 'true'
        
        cmd = ['python', '-m', 'mmm', 'obliterate', input_path, '-o', output_path]
        if paranoid:
            cmd.append('--paranoid')
        if verify:
            cmd.append('--verify')
        if turbo:
            cmd.append('--turbo')
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            return jsonify({'error': 'Processing failed', 'stderr': result.stderr}), 500
        
        return send_file(output_path, as_attachment=True, download_name=f'cleaned_{filename}')
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    return jsonify({'message': 'MMM Cloud API', 'version': '1.0.0'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
