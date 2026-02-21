from database import get_session, Message, Device
from datetime import datetime
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

connected_clients = {}

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "connected_clients": len(connected_clients)
    })

@app.route('/messages', methods=['GET'])
def get_messages():
    """Get message history"""
    device = request.args.get('device')
    limit = int(request.args.get('limit', 50))
    
    session = get_session()
    try:
        query = session.query(Message)
        
        # Filter by device if specified
        if device:
            query = query.filter(
                (Message.sender == device) | (Message.recipient == device)
            )
        
        # Get most recent messages
        messages = query.order_by(Message.timestamp.desc()).limit(limit).all()
        
        return jsonify({
            'messages': [msg.to_dict() for msg in messages],
            'count': len(messages),
            'device': device
        })
    except Exception as e:
        logger.error(f"Error fetching messages: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@app.route('/devices', methods=['GET'])
def get_devices():
    """Get registered devices"""
    session = get_session()
    try:
        devices = session.query(Device).all()
        return jsonify({
            'devices': [{'name': d.name, 'last_seen': d.last_seen.isoformat()} for d in devices],
            'count': len(devices)
        })
    except Exception as e:
        logger.error(f"Error fetching devices: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()

@app.route('/update', methods=['POST'])
def ota_update():
    """OTA endpoint - tells Zero 2W devices to pull latest code from GitHub"""
    device = request.json.get('device', 'all') if request.json else 'all'
    
    count = 0
    for sid, name in connected_clients.items():
        if device == 'all' or name == device:
            socketio.emit('ota_update', {'action': 'git_pull'}, room=sid)
            count += 1
    
    return jsonify({
        "status": "update signal sent",
        "device": device,
        "recipients": count
    })

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
    
    # Update device last_seen in database
    session = get_session()
    try:
        dev = session.query(Device).filter_by(name=device).first()
        if dev:
            dev.last_seen = datetime.utcnow()
        else:
            dev = Device(name=device, last_seen=datetime.utcnow())
            session.add(dev)
        session.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        session.rollback()
    finally:
        session.close()
    
    emit('registered', {'device': device, 'status': 'success'})

@socketio.on('send_emoji')
def handle_send_emoji(data):
    """Route emoji from sender to specific recipient"""
    sender = data.get('sender')
    recipient = data.get('recipient')
    emoji = data.get('emoji')
    text = data.get('text', '')
    
    logger.info(f"📨 {sender} sending {emoji} to {recipient}")
    
    # Save to database
    session = get_session()
    try:
        msg = Message(
            sender=sender,
            recipient=recipient,
            emoji=emoji,
            text=text,
            timestamp=datetime.utcnow()
        )
        session.add(msg)
        session.commit()
        message_id = msg.id
        logger.info(f"💾 Saved to database (ID: {message_id})")
    except Exception as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        message_id = None
    finally:
        session.close()
    
    # Find recipient's socket ID
    recipient_sid = None
    for sid, device_name in connected_clients.items():
        if device_name == recipient:
            recipient_sid = sid
            break
    
    # Send to specific recipient
    if recipient_sid:
        socketio.emit('receive_emoji', {
            'sender': sender,
            'recipient': recipient,
            'emoji': emoji,
            'text': text,
            'message_id': message_id
        }, room=recipient_sid)
        
        emit('emoji_sent', {
            'status': 'delivered',
            'recipient': recipient,
            'message_id': message_id
        })
        logger.info(f"✅ Delivered to {recipient}")
    else:
        emit('emoji_sent', {
            'status': 'recipient_offline',
            'recipient': recipient,
            'message_id': message_id
        })
        logger.warning(f"⚠️ {recipient} is offline")

if __name__ == '__main__':
    logger.info("🚀 Starting server...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
