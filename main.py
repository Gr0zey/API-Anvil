from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import numpy as np
from PIL import Image
import psycopg2
from typing import List, Dict
from dotenv import load_dotenv
import io
import base64
import os

# Charger les variables d'environnement
load_dotenv()

app = FastAPI()

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration DB
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stego_images (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_hidden TEXT,
            processed_image_base64 TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def hide_message_in_image(msg: str, image_path: str) -> bytes:
    """Retourne l'image modifiée en bytes"""
    img = Image.open(image_path)
    data = np.array(img)

    # Convertir le message en binaire
    binary_msg = ''.join(format(ord(c), '08b') for c in msg)
    length = format(len(binary_msg), '016b')
    full_msg = length + binary_msg

    # Insérer le message dans l'image
    msg_index = 0
    for row in data:
        for pixel in row:
            for color in range(3):  # R, G, B
                if msg_index < len(full_msg):
                    # Modifier le LSB
                    pixel[color] = (pixel[color] & 0xFE) | int(full_msg[msg_index])
                    msg_index += 1
                else:
                    break
            if msg_index >= len(full_msg):
                break
        if msg_index >= len(full_msg):
            break

    # Convertir en bytes
    output = io.BytesIO()
    Image.fromarray(data).save(output, format="JPG")
    return output.getvalue()

init_db()

@app.post("/process/")
def process_image():
    try:
        # Chemin vers l'image (même dossier que le code)
        image_path = "image.jpg"
        
        # Vérifier si l'image existe
        try:
            with open(image_path, "rb") as f:
                pass
        except FileNotFoundError:
            raise HTTPException(404, detail="image.jpg non trouvée dans le dossier courant")

        # Générer un message (timestamp)
        timestamp = datetime.now().isoformat()
        
        # Traiter l'image
        processed_image_bytes = hide_message_in_image(timestamp, image_path)
        image_base64 = base64.b64encode(processed_image_bytes).decode('utf-8')

        # Sauvegarder en DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO stego_images (message_hidden, processed_image_base64) VALUES (%s, %s) RETURNING id",
            (timestamp, image_base64)
        )
        record_id = cur.fetchone()[0]
        conn.commit()

        return {
            "id": record_id,
            "message": "Image traitée avec succès",
            "timestamp": timestamp,
            "image_base64": image_base64[:50] + "..."  # Aperçu
        }

    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.get("/results/")
def get_results():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, created_at, message_hidden FROM stego_images ORDER BY created_at DESC")
    results = cur.fetchall()
    return [
        {"id": r[0], "created_at": r[1], "message": r[2]}
        for r in results
    ]
