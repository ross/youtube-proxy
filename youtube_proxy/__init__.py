#!/usr/bin/env python3

# Based loosely off of https://www.deadf00d.com/post/how-i-hacked-sonos-and-youtube-the-same-day.html
# and pytube

from .transcode import Transcoder
from .youtube import YouTube, YouTubeStreamer


from flask import Flask, Response
from http.client import HTTPConnection  # py3
from logging import getLogger
from logging.config import dictConfig
from os import environ


level = environ.get('LOGGING_LEVEL', 'INFO')
if level == 'DEBUG':
    HTTPConnection.debuglevel = 1
dictConfig(
    {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'simple': {
                'format': '%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s %(message)s',
                'datefmt': '%Y-%m-%dT%H:%M:%S',
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': level,
                'formatter': 'simple',
            }
        },
        'root': {'level': level, 'handlers': ('console',)},
        'loggers': {'urllib3.connectionpool': {'level': 'INFO'}},
    }
)


def create_app():
    app = Flask('sonos-proxy')

    @app.route('/<string:vid>')
    def youtube(vid):
        yts = YouTubeStreamer(YouTube(vid))
        return Response(Transcoder(yts).acc_audio(), mimetype='audio/aac')

    getLogger().info('Example URL: http://<host-fqdn>:<port>/jfKfPfyJRdk')
    return app
