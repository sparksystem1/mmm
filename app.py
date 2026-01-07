#!/usr/bin/env python3
"""
MMM Web Interface Backend
Flask server qui connecte l'interface React au code MMM réel
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
import threading
import uuid
import subprocess
import time

app = Flask(__name__)
CORS(app)

# Configuration
UPLOAD_FOLDER = tempfile.mkdtemp()
OUTPUT_FOLDER = tempfile.mkdtemp()
BACKUP_FOLDER = tempfile.mkdtemp()
ALLOWED_EXTENSIONS = {'mp3', 'wav'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['BACKUP_FOLDER'] = BACKUP_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max

# Stockage des jobs de traitement
processing_jobs = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

class ProcessingJob:
    def __init__(self, job_id, files, options):
        self.job_id = job_id
        self.files = files
        self.options = options
        self.status = 'pending'
        self.progress = 0
        self.results = []
        self.error = None

    def to_dict(self):
        return {
            'job_id': self.job_id,
            'status': self.status,
            'progress': self.progress,
            'results': self.results,
            'error': self.error
        }

def build_mmm_command(input_path, output_path, options):
    """Construit la commande mmm CLI avec les options"""
    cmd = ['python', '-m', 'mmm.cli', 'obliterate', input_path, '-o', output_path]
    
    if options.get('paranoid', False):
        cmd.append('--paranoid')
    if options.get('verify', False):
        cmd.append('--verify')
    if options.get('turbo', False):
        cmd.append('--turbo')
    if options.get('backup', False):
        cmd.append('--backup')
    
    output_format = options.get('output_format', 'preserve')
    if output_format != 'preserve':
        cmd.extend(['--format', output_format])
    
    # Advanced flags
    advanced = options.get('advanced_flags', {})
    if advanced.get('gated_resample_nudge'):
        cmd.append('--gated-resample-nudge')
    if advanced.get('phase_noise'):
        cmd.append('--phase-noise')
    if advanced.get('phase_swirl'):
        cmd.append('--phase-swirl')
    if not advanced.get('phase_dither', True):
        cmd.append('--no-phase-dither')
    if not advanced.get('comb_mask', True):
        cmd.append('--no-comb-mask')
    if not advanced.get('transient_shift', True):
        cmd.append('--no-transient-shift')
    if advanced.get('masked_hf_phase'):
        cmd.append('--masked-hf-phase')
    if advanced.get('micro_eq_flutter'):
        cmd.append('--micro-eq-flutter')
    if advanced.get('hf_decorrelate'):
        cmd.append('--hf-decorrelate')
    if advanced.get('refined_transient'):
        cmd.append('--refined-transient')
    if advanced.get('adaptive_transient'):
        cmd.append('--adaptive-transient')
    
    return cmd

def process_file(job_id, file_path, output_path, options):
    """Traite un fichier audio avec MMM via subprocess"""
    try:
        job = processing_jobs[job_id]
        job.status = 'processing'
        
        start_time = time.time()
        
        # Construire et exécuter la commande MMM
        cmd = build_mmm_command(file_path, output_path, options)
        
        print(f"Exécution: {' '.join(cmd)}")
        
        # Exécuter la commande
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes max par fichier
        )
        
        processing_time = time.time() - start_time
        
        if process.returncode != 0:
            raise Exception(f"MMM error: {process.stderr}")
        
        # Analyser la sortie pour extraire les résultats
        output = process.stdout
        
        # Parser les résultats (simplifié - à adapter selon la sortie réelle de MMM)
        result = {
            'file': os.path.basename(file_path),
            'output': os.path.basename(output_path),
            'status': 'success',
            'metadata_removed': extract_number(output, 'metadata', 'tags'),
            'watermarks_detected': extract_number(output, 'watermark', 'detected'),
            'watermarks_removed': extract_number(output, 'watermark', 'removed'),
            'quality_loss': extract_quality(output),
            'processing_time': f"{processing_time:.2f}s",
            'threats': extract_number(output, 'threat', 'remaining'),
            'command': ' '.join(cmd),
            'raw_output': output
        }
        
        job.results.append(result)
        job.progress = 100
        job.status = 'complete'
        
        return result
        
    except subprocess.TimeoutExpired:
        job.status = 'error'
        job.error = 'Timeout - traitement trop long'
        return {'error': 'Timeout', 'file': os.path.basename(file_path)}
    except Exception as e:
        job.status = 'error'
        job.error = str(e)
        print(f"Erreur traitement: {e}")
        return {'error': str(e), 'file': os.path.basename(file_path)}

def extract_number(text, *keywords):
    """Extrait un nombre depuis le texte de sortie MMM"""
    text_lower = text.lower()
    for keyword in keywords:
        if keyword in text_lower:
            # Chercher un nombre après le mot-clé
            import re
            pattern = rf'{keyword}[:\s]+(\d+)'
            match = re.search(pattern, text_lower)
            if match:
                return int(match.group(1))
    return 0

def extract_quality(text):
    """Extrait la perte de qualité depuis la sortie"""
    import re
    match = re.search(r'quality.*?(\d+\.?\d*)\s*%', text.lower())
    if match:
        return f"{match.group(1)}%"
    return "< 2%"

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Upload des fichiers audio"""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    uploaded_files = []
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            file_size = os.path.getsize(filepath)
            uploaded_files.append({
                'name': filename,
                'path': filepath,
                'size': f"{file_size / (1024 * 1024):.2f} MB"
            })
    
    return jsonify({
        'success': True,
        'files': uploaded_files
    })

@app.route('/api/process', methods=['POST'])
def process_audio():
    """Lance le traitement MMM"""
    data = request.json
    
    files = data.get('files', [])
    options = data.get('options', {})
    mode = data.get('mode', 'obliterate')
    
    if not files:
        return jsonify({'error': 'No files to process'}), 400
    
    # Créer un job de traitement
    job_id = str(uuid.uuid4())
    job = ProcessingJob(job_id, files, options)
    processing_jobs[job_id] = job
    
    # Lancer le traitement en arrière-plan
    def process_all():
        for i, file_info in enumerate(files):
            file_path = file_info['path']
            base_name = os.path.basename(file_path)
            output_filename = f"clean_{base_name}"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
            
            result = process_file(job_id, file_path, output_path, options)
            
            # Update progress
            job.progress = int(((i + 1) / len(files)) * 100)
    
    thread = threading.Thread(target=process_all)
    thread.start()
    
    return jsonify({
        'success': True,
        'job_id': job_id
    })

@app.route('/api/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Récupère le statut d'un job"""
    if job_id not in processing_jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = processing_jobs[job_id]
    return jsonify(job.to_dict())

@app.route('/api/analyze', methods=['POST'])
def analyze_audio():
    """Analyse un fichier sans modification"""
    data = request.json
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        # Utiliser la commande analyze de MMM
        cmd = ['python', '-m', 'mmm.cli', 'analyze', file_path]
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if process.returncode != 0:
            return jsonify({'error': process.stderr}), 500
        
        output = process.stdout
        
        return jsonify({
            'success': True,
            'watermarks': extract_number(output, 'watermark'),
            'threats': extract_number(output, 'threat'),
            'metadata': extract_number(output, 'metadata', 'tag'),
            'file': os.path.basename(file_path),
            'raw_output': output
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/<filename>', methods=['GET'])
def download_file(filename):
    """Télécharge un fichier traité"""
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(filepath, as_attachment=True)

@app.route('/api/config/presets', methods=['GET'])
def get_presets():
    """Retourne les presets disponibles"""
    presets = {
        'stealth-plus': {
            'name': 'Stealth Plus',
            'paranoid': True,
            'verify': True,
            'turbo': True,
            'quality': 'high',
            'advanced_flags': {
                'gated_resample_nudge': True,
                'phase_noise': True,
                'phase_swirl': False,
                'phase_dither': False,
                'comb_mask': False,
                'transient_shift': False
            }
        },
        'stealth': {
            'name': 'Stealth',
            'paranoid': True,
            'verify': True,
            'quality': 'high'
        },
        'fast': {
            'name': 'Fast',
            'paranoid': False,
            'verify': False,
            'turbo': True,
            'quality': 'medium'
        },
        'quality': {
            'name': 'Quality',
            'paranoid': False,
            'quality': 'maximum'
        },
        'research': {
            'name': 'Research',
            'paranoid': True,
            'verify': True,
            'quality': 'high',
            'verbose': True
        }
    }
    
    return jsonify(presets)

@app.route('/api/health', methods=['GET'])
def health_check():
    """Vérifie que le serveur et MMM fonctionnent"""
    gpu_available = False
    mmm_available = False
    
    # Check GPU
    try:
        import torch
        gpu_available = torch.cuda.is_available()
    except ImportError:
        pass
    
    # Check MMM
    try:
        result = subprocess.run(
            ['python', '-m', 'mmm.cli', '--help'],
            capture_output=True,
            timeout=5
        )
        mmm_available = result.returncode == 0
    except:
        pass
    
    return jsonify({
        'status': 'ok' if mmm_available else 'error',
        'mmm_available': mmm_available,
        'gpu_available': gpu_available,
        'upload_folder': app.config['UPLOAD_FOLDER'],
        'output_folder': app.config['OUTPUT_FOLDER']
    })

@app.route('/api/clear', methods=['POST'])
def clear_temp():
    """Nettoie les fichiers temporaires"""
    try:
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, BACKUP_FOLDER]:
            if os.path.exists(folder):
                shutil.rmtree(folder)
                os.makedirs(folder)
        
        processing_jobs.clear()
        
        return jsonify({'success': True, 'message': 'Fichiers temporaires nettoyés'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║  🎵 MMM Web Interface Backend - VERSION FONCTIONNELLE     ║
    ║  Melodic Metadata Massacrer                               ║
    ║                                                           ║
    ║  Server: http://localhost:5000                           ║
    ║  Upload: {:<48}║
    ║  Output: {:<48}║
    ║                                                           ║
    ║  ⚡ Traitement RÉEL via subprocess MMM CLI               ║
    ║  🔧 Ouvrez l'interface dans Claude.ai                    ║
    ╚═══════════════════════════════════════════════════════════╝
    """.format(UPLOAD_FOLDER[:48], OUTPUT_FOLDER[:48]))
    
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Upload des fichiers audio"""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    uploaded_files = []
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            file_size = os.path.getsize(filepath)
            uploaded_files.append({
                'name': filename,
                'path': filepath,
                'size': f"{file_size / (1024 * 1024):.2f} MB"
            })
    
    return jsonify({
        'success': True,
        'files': uploaded_files
    })

@app.route('/api/process', methods=['POST'])
def process_audio():
    """Lance le traitement MMM"""
    data = request.json
    
    files = data.get('files', [])
    options = data.get('options', {})
    mode = data.get('mode', 'obliterate')
    
    if not files:
        return jsonify({'error': 'No files to process'}), 400
    
    # Créer un job de traitement
    job_id = str(uuid.uuid4())
    job = ProcessingJob(job_id, files, options)
    processing_jobs[job_id] = job
    
    # Lancer le traitement en arrière-plan
    def process_all():
        for file_info in files:
            file_path = file_info['path']
            output_filename = f"clean_{os.path.basename(file_path)}"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
            
            result = process_file(job_id, file_path, output_path, options)
            
            # Update progress
            job.progress = int((len(job.results) / len(files)) * 100)
    
    thread = threading.Thread(target=process_all)
    thread.start()
    
    return jsonify({
        'success': True,
        'job_id': job_id
    })

@app.route('/api/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Récupère le statut d'un job"""
    if job_id not in processing_jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = processing_jobs[job_id]
    return jsonify(job.to_dict())

@app.route('/api/analyze', methods=['POST'])
def analyze_audio():
    """Analyse un fichier sans modification"""
    data = request.json
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        detector = WatermarkDetector()
        results = detector.detect(file_path)
        
        metadata_cleaner = MetadataCleaner()
        metadata = metadata_cleaner.scan_metadata(file_path)
        
        return jsonify({
            'success': True,
            'watermarks': results.get('watermarks', []),
            'threats': results.get('threats', []),
            'metadata': metadata,
            'file': os.path.basename(file_path)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/<filename>', methods=['GET'])
def download_file(filename):
    """Télécharge un fichier traité"""
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(filepath, as_attachment=True)

@app.route('/api/config/presets', methods=['GET'])
def get_presets():
    """Retourne les presets disponibles"""
    presets = {
        'stealth-plus': {
            'name': 'Stealth Plus',
            'paranoid': True,
            'verify': True,
            'turbo': True,
            'quality': 'high',
            'advanced_flags': {
                'gated_resample_nudge': True,
                'phase_noise': True,
                'phase_swirl': False,
                'phase_dither': False,
                'comb_mask': False,
                'transient_shift': False
            }
        },
        'stealth': {
            'name': 'Stealth',
            'paranoid': True,
            'verify': True,
            'quality': 'high'
        },
        'fast': {
            'name': 'Fast',
            'paranoid': False,
            'verify': False,
            'turbo': True,
            'quality': 'medium'
        },
        'quality': {
            'name': 'Quality',
            'paranoid': False,
            'quality': 'maximum'
        },
        'research': {
            'name': 'Research',
            'paranoid': True,
            'verify': True,
            'quality': 'high',
            'verbose': True
        }
    }
    
    return jsonify(presets)

@app.route('/api/health', methods=['GET'])
def health_check():
    """Vérifie que le serveur fonctionne"""
    gpu_available = False
    try:
        import cupy
        gpu_available = True
    except ImportError:
        pass
    
    return jsonify({
        'status': 'ok',
        'gpu_available': gpu_available,
        'upload_folder': app.config['UPLOAD_FOLDER'],
        'output_folder': app.config['OUTPUT_FOLDER']
    })

if __name__ == '__main__':
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║  🎵 MMM Web Interface Backend                            ║
    ║  Melodic Metadata Massacrer                              ║
    ║                                                           ║
    ║  Server running on http://localhost:5000                 ║
    ║  Upload folder: {:<44}║
    ║  Output folder: {:<44}║
    ╚═══════════════════════════════════════════════════════════╝
    """.format(UPLOAD_FOLDER, OUTPUT_FOLDER))
    
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
