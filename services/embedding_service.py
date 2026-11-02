from core.config import settings

class EmbeddingService:
    _model = None

    @classmethod
    def get_model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer(settings.EMBEDDING_MODEL)
        return cls._model

    @classmethod
    def encode(cls, text: str):
        model = cls.get_model()
        return model.encode(text).tolist()

    @classmethod
    def get_embedding(cls, text: str):
        """Backward-compatible alias for encode."""
        return cls.encode(text)

    @classmethod
    def encode_batch(cls, texts: list[str]):
        model = cls.get_model()
        return model.encode(texts).tolist()

    @classmethod
    def get_dimension(cls) -> int:
        """Returns the output vector dimension for the configured model."""
        model = cls.get_model()
        return model.get_sentence_embedding_dimension()
