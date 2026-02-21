from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True)
    sender = Column(String(50), nullable=False)
    recipient = Column(String(50), nullable=False)
    emoji = Column(String(10), nullable=False)
    text = Column(String(200))
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'sender': self.sender,
            'recipient': self.recipient,
            'emoji': self.emoji,
            'text': self.text,
            'timestamp': self.timestamp.isoformat()
        }

class Device(Base):
    __tablename__ = 'devices'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow)

# Initialize database
db_path = os.path.join(os.path.dirname(__file__), 'db', 'messages.db')
engine = create_engine(f'sqlite:///{db_path}')
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)

def get_session():
    return Session()
