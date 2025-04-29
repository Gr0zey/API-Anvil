from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import datetime
import numpy as np
from PIL import Image
import os
import psycopg2
import io
from typing import List, Dict
from dotenv import load_dotenv
import logging
from pathlib import Path

# Charger les variables d'environnement
load_dotenv()

app = FastAPI()

# Configurer le logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration de la DB depuis .env
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")

# Créer le dossier uploads s'il n'existe pas
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

# Servir les fichiers statiques
app.mount("/uploads", StaticFiles(directory=UPLOAD_FOLDER), name="uploads")

# Fonction de connexion à la DB avec gestion d'erreur
def get_db_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("Connexion à la DB réussie")
        return conn
    except Exception as e:
        logger.error(f"Erreur de connexion à la DB: {e}")
        raise HTTPException(500, detail="Database connection error")

# Initialiser la table
def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS steganography_images (
                id SERIAL PRIMARY KEY,
                original_filename VARCHAR(255),
                processed_filename VARCHAR(255),
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_hidden VARCHAR(255)
            )
        """)
        conn.commit()
        logger.info("Table initialisée avec succès")
    except Exception as e:
        logger.error(f"Erreur d'initialisation de la DB: {e}")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

# Fonction de stéganographie MODIFIÉE
def hide_message_in_image(msg: str, image_data: bytes, output_path: str) -> str:
    """Version modifiée pour accepter des données binaires directement"""
    try:
        # Créer une image à partir des données bytes
        image = Image.open(io.BytesIO(image_data))
        data = np.array(image)

        final_message = ""
        for lettre in msg:
            position_ascii = ord(lettre)
            binaire = bin(position_ascii)[2:]
            while len(binaire) < 8:
                binaire = "0" + binaire
            final_message += binaire

        longueur = len(final_message)
        binaire = bin(longueur)[2:]
        while len(binaire) < 16:
            binaire = "0" + binaire
        result_message = binaire + final_message

        tour = 0
        y = 0
        for line in data:
            x = 0
            for colonne in line:
                rgb = 0
                for couleur in colonne:
                    valeur = data[y][x][rgb]
                    binaire = bin(valeur)[2:]
                    binaire_list = list(binaire)
                    del binaire_list[-1]
                    binaire_list.append(result_message[tour])
                    decimal = int("".join(binaire_list), 2)
                    data[y][x][rgb] = decimal
                    tour += 1
                    rgb += 1
                    if tour >= len(result_message):
                        break
                x += 1
                if tour >= len(result_message):
                    break
            y += 1
            if tour >= len(result_message):
                break

        image_finale = Image.fromarray(data)
        image_finale.save(output_path)
        return output_path
    except Exception as e:
        logger.error(f"Erreur dans hide_message_in_image: {str(e)}")
        raise

# Initialiser la DB au démarrage
init_db()

@app.get("/message/{image_id}")
async def get_hidden_message(image_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT message_hidden FROM steganography_images 
            WHERE id = %s
        """, (image_id,))
        result = cur.fetchone()
        if not result:
            raise HTTPException(404, detail="Image not found")
        return {"hidden_message": result[0]}
    finally:
        cur.close()
        conn.close()

@app.post("/upload/")
async def upload_image(file: UploadFile = File(...)):
    if not file.content_type.startswith('image/'):
        raise HTTPException(400, detail="Le fichier doit être une image")

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    original_filename = f"{UPLOAD_FOLDER}/original_{timestamp}_{file.filename}"
    processed_filename = f"{UPLOAD_FOLDER}/secret_{timestamp}.png"

    try:
        # Lire les données de l'image
        image_data = await file.read()

        # Sauvegarder l'image originale
        with open(original_filename, "wb") as buffer:
            buffer.write(image_data)

        # Cacher le timestamp (en passant directement les bytes)
        hide_message_in_image(timestamp, image_data, processed_filename)

        # Sauvegarder dans la DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO steganography_images 
            (original_filename, processed_filename, message_hidden) 
            VALUES (%s, %s, %s) RETURNING id""",
            (original_filename, processed_filename, timestamp)
        )
        image_id = cur.fetchone()[0]
        conn.commit()

        return {
            "id": image_id,
            "original_filename": original_filename,
            "processed_filename": processed_filename,
            "message_hidden": timestamp,
            "download_url": f"/download/{Path(processed_filename).name}"
        }
    except Exception as e:
        logger.error(f"Erreur lors du traitement: {e}")
        raise HTTPException(500, detail=str(e))
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Endpoint pour télécharger les images traitées"""
    file_path = f"{UPLOAD_FOLDER}/{filename}"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Fichier non trouvé")
    return FileResponse(file_path)

@app.get("/images/")
def list_images() -> List[Dict]:
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, original_filename, processed_filename, 
                   upload_time, message_hidden 
            FROM steganography_images
        """)
        return [
            {
                "id": img[0],
                "original_filename": img[1],
                "processed_filename": img[2],
                "upload_time": img[3],
                "message_hidden": img[4]
            }
            for img in cur.fetchall()
        ]
    except Exception as e:
        logger.error(f"Erreur DB: {e}")
        raise HTTPException(500, detail="Database error")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

@app.get("/")
def read_root():
    return {"message": "API de stéganographie"}