from flask import Flask, request
from flask_socketio import SocketIO, emit
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

connected_clients = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"✅ Client connected: {request.sid}")
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"❌ Client disconnected: {request.sid}")
    if request.sid in connected_clients:
        del connected_clients[request.sid]

@socketio.on('register')
def handle_register(data):
    device = data.get('device_name')
    connected_clients[request.sid] = device
    logger.info(f"📝 Registered: {device}")
    emit('registered', {'device': device, 'status': 'success'})

@socketio.on('send_message')
def handle_message(data):
    logger.info(f"📨 Message: {data}")
    emit('message_received', data, broadcast=True)

if __name__ == '__main__':
    logger.info("🚀 Starting server...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
