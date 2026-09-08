import abc

class BaseDBProvider(abc.ABC):
    """
    Abstract base class for Database Providers
    """

    @abc.abstractmethod
    def get_connection(self):
        """
        Returns a MySQL database connection.
        """
        pass
