# COMPLIANT - secrets from environment
import os

API_KEY = os.environ.get("API_KEY")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD")

def connect():
    return API_KEY
