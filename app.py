#!/usr/bin/env python3
"""
Catalog PDF Filter - Web Service
Wraps catalog_filter.py in a Flask API for use from n8n (HTTP Request node).

Endpoints:
  GET  /            - health check
  POST /filter       - multipart/form-data: 'pdf' (file), 'numbers' (text) -> filtered PDF
"""

import os
import tempfile
import traceback
import logging

from flask import Flask, request, send_file, jsonify

from catalog_filter import create_filtered_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Render free tier: keep uploads reasonably small (catalog PDFs, not huge scans)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25 MB


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "catalog-pdf-filter"})


@app.route('/filter', methods=['POST'])
def filter_catalog():
    try:
        numbers = (request.args.get('numbers') or request.form.get('numbers') or '').strip()

        pdf_bytes = request.get_data()

        # Fallback: also support classic multipart upload (e.g. manual curl -F testing)
        if not pdf_bytes and 'pdf' in request.files:
            pdf_bytes = request.files['pdf'].read()

        if not pdf_bytes:
            return jsonify({"error": "PDF не получен (пустое тело запроса)"}), 400

        if not numbers:
            return jsonify({"error": "Не переданы номера позиций (параметр 'numbers')"}), 400

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.pdf')
            output_path = os.path.join(tmpdir, 'output.pdf')
            with open(input_path, 'wb') as f:
                f.write(pdf_bytes)

            logger.info(f"Processing catalog, numbers='{numbers}', size={len(pdf_bytes)} bytes")
            success, message = create_filtered_pdf(input_path, numbers, output_path)
            logger.info(f"Result: success={success} message={message}")

            if not success:
                return jsonify({"error": message}), 400

            return send_file(
                output_path,
                mimetype='application/pdf',
                as_attachment=True,
                download_name='selection.pdf'
            )

    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Внутренняя ошибка сервиса: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
