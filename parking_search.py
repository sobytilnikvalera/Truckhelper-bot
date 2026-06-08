import requests
import math
import logging

logger = logging.getLogger(__name__)


class ParkingSearch:
    def __init__(self):
        self.overpass_url = "http://overpass-api.de/api/interpreter"

    def _calculate_distance(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two points in km (Haversine)."""
        R = 6371
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def search_parking(self, latitude, longitude, radius=50000):
        """Search for truck parking using multiple Overpass queries for better coverage."""
        all_parkings = {}

        # Query 1: Direct truck/HGV parking
        query1 = f'[out:json][timeout:25];(nwr["amenity"="parking"]["hgv"](around:{radius},{latitude},{longitude});nwr["amenity"="parking"]["capacity:hgv"](around:{radius},{latitude},{longitude});nwr["amenity"="parking"]["parking"="truck"](around:{radius},{latitude},{longitude}););out center;'

        # Query 2: Rest areas and service stations
        query2 = f'[out:json][timeout:25];(nwr["highway"="rest_area"](around:{radius},{latitude},{longitude});nwr["highway"="services"](around:{radius},{latitude},{longitude});nwr["amenity"="fuel"]["hgv"="yes"](around:{radius},{latitude},{longitude});nwr["amenity"="fuel"]["hgv"="designated"](around:{radius},{latitude},{longitude}););out center;'

        # Query 3: Broader parking (large ones near highways)
        query3 = f'[out:json][timeout:25];(nwr["amenity"="parking"]["capacity"~"^[5-9][0-9]|[1-9][0-9][0-9]"](around:{min(radius, 25000)},{latitude},{longitude});nwr["tourism"="caravan_site"](around:{radius},{latitude},{longitude}););out center;'

        queries = [query1, query2, query3]

        for query in queries:
            try:
                response = requests.post(self.overpass_url, data={"data": query},
                                         headers={"User-Agent": "TruckHelperBot/1.0"},
                                         timeout=30)
                response.raise_for_status()
                data = response.json()

                for element in data.get("elements", []):
                    lat = element.get("lat") or (element.get("center") or {}).get("lat")
                    lon = element.get("lon") or (element.get("center") or {}).get("lon")

                    if lat is None or lon is None:
                        continue

                    # Unique key to avoid duplicates
                    elem_id = f"{element.get('type', '')}_{element.get('id', '')}"
                    if elem_id in all_parkings:
                        continue

                    tags = element.get("tags", {})
                    name = tags.get("name", "")
                    if not name:
                        if tags.get("highway") == "rest_area":
                            name = "Зона отдыха"
                        elif tags.get("highway") == "services":
                            name = "Сервисная зона"
                        elif tags.get("amenity") == "fuel":
                            brand = tags.get("brand", tags.get("operator", ""))
                            name = f"АЗС {brand}".strip() if brand else "АЗС"
                        elif tags.get("tourism") == "caravan_site":
                            name = "Кемпинг"
                        else:
                            name = "Парковка"

                    distance = self._calculate_distance(latitude, longitude, lat, lon)

                    amenities = {
                        "shower": tags.get("shower") == "yes" or tags.get("hgv:shower") == "yes",
                        "wc": tags.get("toilets") == "yes" or tags.get("hgv:toilets") == "yes" or tags.get("toilet") == "yes",
                        "fuel": tags.get("amenity") == "fuel" or tags.get("fuel") == "yes",
                        "restaurant": tags.get("restaurant") == "yes" or tags.get("food") == "yes" or tags.get("diner") == "yes",
                    }

                    all_parkings[elem_id] = {
                        "name": name,
                        "latitude": lat,
                        "longitude": lon,
                        "distance": distance,
                        "amenities": amenities,
                    }

            except requests.exceptions.RequestException as e:
                logger.warning(f"Overpass query failed: {e}")
                continue
            except Exception as e:
                logger.warning(f"Parking search error: {e}")
                continue

        # Sort by distance
        parkings = list(all_parkings.values())
        parkings.sort(key=lambda p: p["distance"])
        return parkings


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    finder = ParkingSearch()
    lat, lon = 52.5200, 13.4050
    print(f"Searching near {lat}, {lon}...")
    results = finder.search_parking(lat, lon, radius=50000)
    print(f"Found {len(results)} results")
    for i, p in enumerate(results[:10], 1):
        amenities = []
        if p["amenities"]["shower"]: amenities.append("🚿")
        if p["amenities"]["wc"]: amenities.append("🚻")
        if p["amenities"]["fuel"]: amenities.append("⛽")
        if p["amenities"]["restaurant"]: amenities.append("🍽")
        print(f"{i}. {p['name']} — {p['distance']:.1f} км {' '.join(amenities)}")
