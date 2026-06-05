
import requests
import math

class ParkingSearch:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.overpass_url = "http://overpass-api.de/api/interpreter"

    def _calculate_distance(self, lat1, lon1, lat2, lon2):
        R = 6371  # Radius of Earth in kilometers

        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad

        a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        distance = R * c
        return distance

    def search_truck_parking_osm(self, latitude, longitude, radius=25000):
        # Expanded Overpass API query to find various types of truck parking
        # Radius increased to 25km as requested
        query = f"""
        [out:json];
        (
          node["hgv"="yes"]["amenity"="parking"](around:{radius},{latitude},{longitude});
          way["hgv"="yes"]["amenity"="parking"](around:{radius},{latitude},{longitude});
          relation["hgv"="yes"]["amenity"="parking"](around:{radius},{latitude},{longitude});

          node["amenity"="parking"]["capacity:hgv"](around:{radius},{latitude},{longitude});
          way["amenity"="parking"]["capacity:hgv"](around:{radius},{latitude},{longitude});
          relation["amenity"="parking"]["capacity:hgv"](around:{radius},{latitude},{longitude});

          node["amenity"="parking"]["parking"="truck"](around:{radius},{latitude},{longitude});
          way["amenity"="parking"]["parking"="truck"](around:{radius},{latitude},{longitude});
          relation["amenity"="parking"]["parking"="truck"](around:{radius},{latitude},{longitude});

          node["highway"="rest_area"](around:{radius},{latitude},{longitude});
          way["highway"="rest_area"](around:{radius},{latitude},{longitude});
          relation["highway"="rest_area"](around:{radius},{latitude},{longitude});

          node["highway"="services"](around:{radius},{latitude},{longitude});
          way["highway"="services"](around:{radius},{latitude},{longitude});
          relation["highway"="services"](around:{radius},{latitude},{longitude});

          node["tourism"="caravan_site"](around:{radius},{latitude},{longitude});
          way["tourism"="caravan_site"](around:{radius},{latitude},{longitude});
          relation["tourism"="caravan_site"](around:{radius},{latitude},{longitude});

          node["amenity"="fuel"]["hgv"="yes"](around:{radius},{latitude},{longitude});
          way["amenity"="fuel"]["hgv"="yes"](around:{radius},{latitude},{longitude});
          relation["amenity"="fuel"]["hgv"="yes"](around:{radius},{latitude},{longitude});

          node["landuse"="commercial"]["parking"](around:{radius},{latitude},{longitude});
          way["landuse"="commercial"]["parking"](around:{radius},{latitude},{longitude});
          relation["landuse"="commercial"]["parking"](around:{radius},{latitude},{longitude});
        );
        out center;
        """

        try:
            response = requests.post(self.overpass_url, data=query)
            response.raise_for_status() # Raise an exception for HTTP errors
            data = response.json()
            parkings = []
            for element in data.get("elements", []):
                if element.get("type") in ["node", "way", "relation"]:
                    lat = element.get("lat", element.get("center", {}).get("lat"))
                    lon = element.get("lon", element.get("center", {}).get("lon"))
                    
                    if lat is None or lon is None:
                        continue

                    name = element.get("tags", {}).get("name", "Без названия")
                    distance = self._calculate_distance(latitude, longitude, lat, lon)
                    
                    # Extract amenities
                    tags = element.get("tags", {})
                    amenities = {
                        "shower": tags.get("hgv:shower", tags.get("shower", "no")) == "yes",
                        "wc": tags.get("hgv:toilets", tags.get("toilets", "no")) == "yes",
                        "fuel": tags.get("fuel", "no") == "yes" or tags.get("amenity", "") == "fuel",
                        "restaurant": tags.get("restaurant", "no") == "yes" or tags.get("diner", "no") == "yes"
                    }

                    parkings.append({
                        "name": name,
                        "latitude": lat,
                        "longitude": lon,
                        "distance": distance,
                        "amenities": amenities,
                        "tags": tags # Keep original tags for debugging/future use
                    })
            
            # Sort by distance
            parkings.sort(key=lambda p: p["distance"])
            return parkings
        except requests.exceptions.RequestException as e:
            print(f"Error during Overpass API request: {e}")
            return []

    def search_parking(self, latitude, longitude, radius=25000):
        # For now, only OSM search is implemented and enhanced.
        return self.search_truck_parking_osm(latitude, longitude, radius)

if __name__ == '__main__':
    parking_finder = ParkingSearch()
    # Coordinates for a location in Europe (e.g., Berlin)
    lat = 52.5200
    lon = 13.4050
    print(f"Searching for truck parking near {lat}, {lon} using OpenStreetMap...")
    parkings = parking_finder.search_parking(lat, lon, radius=25000) # 25 km radius
    if parkings:
        for i, parking in enumerate(parkings[:10]): # Show top 10
            amenities_str = []
            if parking["amenities"]["shower"]: amenities_str.append("Душ")
            if parking["amenities"]["wc"]: amenities_str.append("Туалет")
            if parking["amenities"]["fuel"]: amenities_str.append("Топливо")
            if parking["amenities"]["restaurant"]: amenities_str.append("Ресторан")
            amenities_display = f" ({', '.join(amenities_str)})" if amenities_str else ""

            print(f"{i+1}. {parking['name']} ({parking['distance']:.2f} км){amenities_display}")
            print(f"   📍 https://www.google.com/maps/search/?api=1&query={parking['latitude']},{parking['longitude']}")
    else:
        print("No truck parking found.")
