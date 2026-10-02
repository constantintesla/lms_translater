import uvicorn

from . import config

uvicorn.run("dubservice.app:app", host=config.HOST, port=config.PORT)
