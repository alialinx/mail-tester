from pydantic import BaseModel


class UserRegister(BaseModel):
    email: str
    password: str



class ApiKeyCreate(BaseModel):
    name: str = None
