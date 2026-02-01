import logging
import os

class CustomFormatter(logging.Formatter):
    """
    Handles colors and formatting for the logger output.
    """

    def __init__(self):
        
        self.COLORS = {
            'grey': '\x1b[38;21m',
            'blue': '\x1b[38;5;39m',
            'yellow': '\x1b[38;5;226m',
            'red': '\x1b[38;5;196m',
            'reset': '\x1b[0m'
        }
        
        # Logger String Format
        self.log_fmt = f"%(asctime)s - %(levelname)s - %(name)s - %(message)s (%(filename)s:%(lineno)d)"

    def format(self, record):
        """
        Format the logger.
        """

        # Select color based on level
        level_colors = {
            logging.DEBUG: self.COLORS['grey'],
            logging.INFO: self.COLORS['blue'],
            logging.WARNING: self.COLORS['yellow'],
            logging.ERROR: self.COLORS['red']
        }
        
        color = level_colors.get(record.levelno, self.COLORS['reset'])
        formatter = logging.Formatter(color + self.log_fmt + self.COLORS['reset'])
        return formatter.format(record)

class BaseLogger():
    """
    Base Logger that manages separate outputs for console (colored) and file (plain).
    """
    def __init__(self, log_name, log_level):

        self.logger = logging.getLogger(log_name)
        # Avoid duplicate logs
        self.logger.propagate = False

        # Remove handlers from root logger
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        # Set up console handler for logging to console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(CustomFormatter())
        self.logger.addHandler(console_handler)

        # Set up file handler for logging to a file
        filename = f"{log_name}.log"
        file_handler = logging.FileHandler(filename, mode='w')
        file_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        file_handler.setFormatter(file_fmt)
        self.logger.addHandler(file_handler)

        # Set the requested global log level
        self.logger.setLevel(log_level)