from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from vocab_app import vocab_app

root_app = Flask(__name__)

@root_app.route("/")
def index():
	return "Main site"

application = DispatcherMiddleware(
	root_app, 
	{
    "/vocab": vocab_app(),
	}
)

