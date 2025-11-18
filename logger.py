import logging
import sys

def setup_logging():
    """
    Configures the logging for the application.
    """
    # Define the log format
    log_format = '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    
    # Configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Create a logger for this module to confirm setup
    logger = logging.getLogger(__name__)
    logger.info("Logging configured successfully via logger.py.")

def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger instance with the given name.
    """
    return logging.getLogger(name)

