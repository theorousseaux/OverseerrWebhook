from flask import Flask, request, jsonify
from pyarr import SonarrAPI
from dotenv import load_dotenv
import time
import json
import os
import datetime
import logging

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("sonarr_webhook")

# Load environment variables
load_dotenv()
sonarr_url = os.getenv("SONARR_URL", "http://localhost:8989")
api_key = os.getenv("SONARR_API_KEY")
flask_run_port = int(os.getenv("FLASK_RUN_PORT", 5056))

# Check required variables
if not api_key:
    logger.error("SONARR_API_KEY is missing from environment variables")
    # Comment the line below if you want to keep the hardcoded API key for testing
    # exit(1)
    api_key = "aca18be9438d451fad817d2729d40d95"  # Fallback for testing

sonarr = SonarrAPI(sonarr_url, api_key)
app = Flask(__name__)

def get_serie_details(tvdbId):
    """Retrieves series details by its TVDB ID"""
    try:
        return sonarr.get_series(tvdbId, tvdb=True)[0]
    except Exception as e:
        logger.error(f"Error retrieving series with TVDB ID {tvdbId}: {e}")
        return None

def is_future_season(season):
    """
    Determines if a season is a future season (not yet started airing).
    
    A season is considered a future season if:
    1. It has nextAiring but no previousAiring (indicating it hasn't started yet)
    2. It has no episodes downloaded yet
    
    Args:
        season (dict): The season data from Sonarr API
        
    Returns:
        bool: True if it's a future season, False otherwise
    """
    if not "statistics" in season:
        return False
    
    stats = season["statistics"]
    
    # Case 1: No nextAiring - can't be a future season if nothing is scheduled
    if not "nextAiring" in stats:
        return False
    
    # Case 2: Has previousAiring - this season has already started airing
    if "previousAiring" in stats:
        return False
    
    # Case 3: No previousAiring but has nextAiring - this is a future season that hasn't started
    # Optionally, also check if no episodes have been downloaded yet
    return stats.get("episodeFileCount", 0) == 0

def is_currently_airing(season):
    """Checks if a season is currently airing"""
    if not "statistics" in season:
        return False
        
    # Has next airing date - definitely airing
    if "nextAiring" in season["statistics"]:
        try:
            next_airing = datetime.datetime.strptime(
                season["statistics"]["nextAiring"], "%Y-%m-%dT%H:%M:%SZ"
            )
            days_until_next = (next_airing - datetime.datetime.now()).days
            # If next episode is within 30 days, it's currently airing
            return days_until_next <= 30
        except Exception as e:
            logger.error(f"Error analyzing next airing date: {e}")
    
    # Check previous airing if next airing doesn't exist
    if "previousAiring" in season["statistics"]:
        try:
            previous_airing = datetime.datetime.strptime(
                season["statistics"]["previousAiring"], "%Y-%m-%dT%H:%M:%SZ"
            )
            time_since_airing = (datetime.datetime.now() - previous_airing).days
            # If the last episode aired less than 7 days ago, consider it still airing
            return time_since_airing <= 7
        except Exception as e:
            logger.error(f"Error analyzing previous airdate: {e}")
    
    return False

def is_season_incomplete(season):
    """Checks if a season is incomplete"""
    if not "statistics" in season:
        return False
        
    total_episodes = season["statistics"].get("totalEpisodeCount", 0)
    downloaded_episodes = season["statistics"].get("episodeFileCount", 0)
    
    return downloaded_episodes < total_episodes

def find_monitored_currently_airing_season(seasons):
    """Find the season that's currently airing from a list of seasons"""
    # First, look for seasons that are both monitored and currently airing
    airing_seasons = [
        season for season in seasons 
        if season.get("monitored") and 
        is_currently_airing(season) and 
        not is_future_season(season)
    ]
    
    if not airing_seasons:
        return None
        
    # If multiple seasons are airing, prefer the one with the highest season number
    return max(airing_seasons, key=lambda s: s["seasonNumber"])

def wait_with_feedback(seconds, message):
    """Wait with countdown display"""
    logger.info(f"{message}. Waiting for {seconds} seconds...")
    # For production, use full wait
    time.sleep(seconds)
    # For testing/development, uncomment the line below and comment the previous one
    # time.sleep(min(seconds, 5))  # Maximum wait of 5 seconds for testing

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        logger.info(f"Webhook received: {json.dumps(data, ensure_ascii=False)}")
        
        # Check if data contains media information
        if not data.get("media"):
            return jsonify({"status": "No media in request"}), 200
            
        # Check if it's a TV show
        if data["media"].get("media_type") != "tv":
            logger.info("Request is not for a TV show")
            return jsonify({"status": "Not a TV show"}), 200
            
        tvdbId = data["media"].get("tvdbId")
        if not tvdbId:
            return jsonify({"status": "Missing TVDB ID"}), 400
            
        # Wait a few seconds for Sonarr to process the request first
        wait_with_feedback(3, "Initial wait for Sonarr processing")
        
        # Get series details
        serie = get_serie_details(tvdbId)
        if not serie:
            return jsonify({"status": "Series not found"}), 404
            
        serie_id = serie["id"]
        serie_title = serie["title"]
        
        # Find the latest season
        if not serie.get("seasons"):
            return jsonify({"status": "No seasons found"}), 200
            
        # Count monitored seasons
        seasons_monitored = [season for season in serie["seasons"] if season.get("monitored")]
        seasons_monitored_count = len(seasons_monitored)
        
        # Identify incomplete seasons
        seasons_not_complete = [
            season["seasonNumber"] for season in serie["seasons"] 
            if season.get("monitored") and is_season_incomplete(season)
        ]
        
        if seasons_not_complete:
            logger.info(f"Incomplete seasons for '{serie_title}': {seasons_not_complete}")
        
        # Wait for Sonarr to finish its initial searches
        # Adjust wait time based on the number of seasons and their size
        wait_time = 90 * seasons_monitored_count
        wait_with_feedback(wait_time, f"Waiting for processing of {seasons_monitored_count} monitored seasons")
        
        # Get series information again to have the most recent data
        serie = get_serie_details(tvdbId)
        if not serie:
            return jsonify({"status": "Series not found after waiting"}), 404
            
        # Find the currently airing season, if any
        currently_airing_season = find_monitored_currently_airing_season(serie["seasons"])
        
        if not currently_airing_season:
            logger.info(f"No monitored currently airing season found for '{serie_title}'")
            return jsonify({"status": "No currently airing season found"}), 200
            
        season_number = currently_airing_season["seasonNumber"]
        logger.info(f"Found currently airing season {season_number} for '{serie_title}'")
            
        # Check if the season is complete
        if not is_season_incomplete(currently_airing_season):
            logger.info(f"Season {season_number} of '{serie_title}' is complete")
            return jsonify({"status": "Season complete"}), 200
            
        # The season is monitored, incomplete and currently airing
        logger.info(f"Season {season_number} of '{serie_title}' is monitored, currently airing and incomplete")
        
        # Get all episodes and filter those from the currently airing season
        try:
            all_episodes = sonarr.get_episodes_by_series_id(serie_id)
            episodes_to_search = [
                episode["id"] for episode in all_episodes 
                if episode["seasonNumber"] == season_number and 
                not episode.get("hasFile", False)
            ]
            
            if not episodes_to_search:
                logger.info(f"No episodes to search for season {season_number}")
                return jsonify({"status": "No episodes to search"}), 200
                
            logger.info(f"Searching for {len(episodes_to_search)} episodes for season {season_number}")
            
            # Launch episode search
            response = sonarr.post_command("EpisodeSearch", episodeIds=episodes_to_search)
            logger.info(f"Search command sent: {json.dumps(response, indent=2, ensure_ascii=False)}")
            
            return jsonify({
                "status": "Search launched", 
                "episodes_count": len(episodes_to_search),
                "season": season_number,
                "serie": serie_title
            }), 200
            
        except Exception as e:
            logger.error(f"Error searching for episodes: {e}")
            return jsonify({"status": "Error searching for episodes", "error": str(e)}), 500
            
    except Exception as e:
        logger.error(f"General error: {e}")
        return jsonify({"status": "Error", "error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Simple health check endpoint to verify the service is running"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "service": "sonarr-webhook"
    }), 200

if __name__ == "__main__":
    logger.info(f"Starting webhook server on port {flask_run_port}")
    app.run(host="0.0.0.0", port=flask_run_port)