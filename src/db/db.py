from pymongo import MongoClient

from config import MONGO_URI, MONGO_DB_NAME

client = MongoClient(MONGO_URI)
def get_db():
    return client[MONGO_DB_NAME]
