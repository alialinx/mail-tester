from openai import BaseModel


class UserRegister(BaseModel):
    email: str
    password: str

