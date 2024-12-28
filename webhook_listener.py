from flask import Flask, request, jsonify
from pyarr import SonarrAPI
from dotenv import load_dotenv
import time
import json
import os

load_dotenv()
sonarr_url = os.getenv('SONARR_URL')
api_key = os.getenv('SONARR_API_KEY')
flask_run_port = os.getenv('FLASK_RUN_PORT')

sonarr = SonarrAPI(sonarr_url, api_key)

app = Flask(__name__)

# Define a route for '/webhook' that only accepts POST requests
@app.route('/webhook', methods=['POST'])
def webhook():
    # Get the JSON data sent in the POST request
    data = request.json
    # Display the received data for verification
    print('Webhook received:', data)

    if data['media'] is None:
        return jsonify({'status': 'Webhook processed'}), 200
    
    if data['media']['media_type'] == 'tv':
        print('TV show request detected')
        tvdbId = data['media']['tvdbId'] # TVDB ID
        time.sleep(3)
        serie = sonarr.get_series(tvdbId, tvdb=True)[0]
        serie_id = serie['id']

        seasons_not_complete = [] # List of seasons that are not complete
        for season in serie['seasons']:
            if season['monitored'] == True and season['statistics']['episodeFileCount'] != season['statistics']['totalEpisodeCount']:
                seasons_not_complete.append(season['seasonNumber'])
        print("Waiting for serie {} season(s) {} to be added".format(serie['title'], seasons_not_complete))

        start = time.time()
        current = time.time()
        number_of_seasons = len(seasons_not_complete)
        while current - start < 90 * number_of_seasons and seasons_not_complete: # Wait for 90 seconds per season to be added
            queue = sonarr.get_queue() # Get the queue
            if queue['records']:
                for record in queue['records']:
                    if (record['seriesId'] == serie_id) and (record['seasonNumber'] in seasons_not_complete):
                        # as sonarr only looks for entire season pack, we consider the season as complete
                        print("Episode of season {} detected in queue".format(record['seasonNumber']))
                        seasons_not_complete.remove(record['seasonNumber'])
       
            time.sleep(5)
            current = time.time()

        if not seasons_not_complete:
            print("All the required seasons are in the queue")
            return jsonify({'status': 'Webhook processed'}), 200
        else:
            # If some seasons are still missing, we search for the episodes
            print("Some seasons are missing : {}".format(seasons_not_complete))
            all_episodes = sonarr.get_episodes_by_series_id(serie_id)
            episodes_ids_wanted = [] # List of episodes to search
            for episode in all_episodes:
                if episode['seasonNumber'] in seasons_not_complete:
                    print("Searching for episode {}".format(episode['title']))
                    episodes_ids_wanted.append(episode['id'])
            time.sleep(5)
            response = sonarr.post_command('EpisodeSearch', episodeIds=episodes_ids_wanted)
            print(json.dumps(response, indent=2))
            print("Episode search command sent")
            return jsonify({'status': 'Webhook processed'}), 200

    else:
        print("Not a TV show")
        return jsonify({'status': 'Webhook processed'}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=flask_run_port)
