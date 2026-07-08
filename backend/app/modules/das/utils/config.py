from fastapi import FastAPI
from fastapi.security import HTTPBearer
from typing import Dict, Any

DEBUG_MODE = True

DB_CONFIG = {
    'user': 'root',
    'password': '123456',
    'host': '127.0.0.1',
    'port': '3306',
    'database': 'helpdesk'
}

import urllib.parse

password_escaped = urllib.parse.quote(DB_CONFIG['password'])
DATABASE_URL = f"mysql+pymysql://{DB_CONFIG['user']}:{password_escaped}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"

security = HTTPBearer()

AUTH_SERVICE_BASE_URL = "http://localhost:8001/AAS/auth"

PARTITION_CONFIG = {
    'check_interval_days': 3,
    'future_days_to_check': 7,
    'table_name': 'collection_data',
    'partition_column': 'start_time_int',
    'partition_prefix': 'p'
}

MQTT_CONFIG = {
    'broker': '125.122.97.107',
    'port': 8084,
    'username': 'test',
    'password': 'qazokm1029.',
    'client_id': 'DAS_MQTT_WX',
    'keepalive': 60
}

MQTT_PROTOCOL_CONFIG = {
    'version': 'V2.0.0',
    'header_id': 1
}

HEADER_ID_FILE = 'mqtt_header_id.json'