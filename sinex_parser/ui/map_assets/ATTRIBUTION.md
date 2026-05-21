Stations map offline assets

- `leaflet/leaflet.js` and `leaflet/leaflet.css` are Leaflet 1.9.3 assets vendored from the official jsDelivr distribution.
- `jquery-3.7.1.min.js` is vendored jQuery from the official jQuery distribution and is required by Folium popup rendering.
- `ne_50m_land.geojson`, `ne_50m_coastline.geojson`, and `ne_50m_admin_0_boundary_lines_land.geojson` are Natural Earth 1:50m datasets and are public domain.
- `ne_110m_land.geojson` remains in the bundle as an older coarse fallback asset.
- These files support the fully offline Folium stations map used by `StationsWidget`.