from pymongo import MongoClient

from src.config import MONGODB_URI, MONGO_DB_NAME

client = MongoClient(MONGODB_URI)
def get_db():
    return client[MONGO_DB_NAME]
