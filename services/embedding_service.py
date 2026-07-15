from sentence_transformers import SentenceTransformer
from core.config import settings

class EmbeddingService:
    _model = None

    @classmethod
    def get_model(cls):
        if cls._model is None:
            cls._model = SentenceTransformer(settings.EMBEDDING_MODEL)
        return cls._model

    @classmethod
    def encode(cls, text: str):
        model = cls.get_model()
        return model.encode(text).tolist()

    @classmethod
    def encode_batch(cls, texts: list[str]):
        model = cls.get_model()
        return model.encode(texts).tolist()
