from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import pandas as pd
import numpy as np
from utils import analyze_hospital_claims
import logging
import os
import socket

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# Debug: Print all registered routes
@app.on_event("startup")
async def startup_event():
    logging.info("Registered routes:")
    for route in app.routes:
        if hasattr(route, 'methods'):
            logging.info(f"{route.methods} {route.path}")
        else:
            logging.info(f"Mount: {route.path}")

def get_server_ip():
    try:
        # Get all network interfaces
        interfaces = socket.getaddrinfo(host=socket.gethostname(), port=None, family=socket.AF_INET)
        # Filter for non-localhost IPs
        ip_addresses = [ip[4][0] for ip in interfaces if not ip[4][0].startswith('127.')]
        
        if ip_addresses:
            # Use the first non-localhost IP
            return ip_addresses[0]
        return "localhost"
    except Exception as e:
        logging.error(f"Error getting server IP: {e}")
        return "localhost"

# Get the server IP
SERVER_IP = get_server_ip()
logging.info(f"Server IP: {SERVER_IP}")

# Configure CORS with specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600  # Cache preflight requests for 1 hour
)

# Add endpoint to get server IP
@app.get("/api/server-info")
async def get_server_info(request: Request):
    # Get the client's host
    client_host = request.headers.get("host", "").split(":")[0]
    
    # Determine if we're in production
    is_production = client_host != "localhost"
    
    if is_production:
        base_url = f"https://{client_host}"
    else:
        base_url = f"http://localhost:5002"
    
    return {
        "ip": SERVER_IP,
        "port": 5002,
        "base_url": base_url,
        "network": "production" if is_production else "local"
    }

# Add favicon endpoint
@app.get("/favicon.ico")
async def get_favicon():
    try:
        return FileResponse("../frontend/HospIntel/public/favicon.ico")
    except Exception as e:
        logging.error(f"Error serving favicon: {e}")
        return JSONResponse(status_code=404, content={"message": "Favicon not found"})

# Load dataset
try:
    # Get the absolute path to the Excel file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(current_dir, "hospital_data_v1.xlsx")
    logger.info(f"Attempting to load Excel file from: {excel_path}")
    
    if not os.path.exists(excel_path):
        alternative_path = "Backend/hospital_data_v1.xlsx"
        if os.path.exists(alternative_path):
            excel_path = alternative_path
            logger.info(f"Using alternative path: {excel_path}")
        else:
            raise FileNotFoundError(f"Excel file not found at either {excel_path} or {alternative_path}")
    
    df = pd.read_excel(excel_path)
    logger.info(f"Successfully loaded Excel file with shape: {df.shape}")
    logger.info(f"DataFrame columns: {df.columns.tolist()}")
    logger.info(f"Available Partner IDs: {df['PARTNER_ID'].unique().tolist()}")
    
    # Verify required columns
    required_columns = ['PARTNER_ID', 'HOSPITAL', 'HOSP_TYPE', 'CITY', 'STATE', 'PIN']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns in Excel file: {missing_columns}")
    
except FileNotFoundError as e:
    logging.error(f"File not found error: {e}")
    raise
except ValueError as e:
    logging.error(f"Data validation error: {e}")
    raise
except Exception as e:
    logging.error(f"Error loading dataset: {e}")
    raise

# Add a test endpoint to verify API is working
@app.get("/api/test")
async def test_endpoint():
    if df is None:
        return {"status": "error", "message": "Dataset not loaded"}
    return {"status": "ok", "message": "API is working", "data_loaded": True}

@app.get("/api/claims-analysis/{partner_id}")
async def claims_analysis(partner_id: int):
    try:
        if df is None:
            raise HTTPException(status_code=500, detail="Dataset not loaded")
            
        logger.info(f"Received request for partner_id: {partner_id}")
        
        if 'PARTNER_ID' not in df.columns:
            raise HTTPException(status_code=500, detail="PARTNER_ID column not found in dataset")

        available_ids = df['PARTNER_ID'].unique().tolist()
        logger.debug(f"Available Partner IDs: {available_ids}")

        if partner_id not in available_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Hospital with Partner ID {partner_id} not found"
            )

        partner_data = df[df['PARTNER_ID'] == partner_id]
        logger.debug(f"Found {len(partner_data)} records for partner_id {partner_id}")
        
        results = analyze_hospital_claims(df, partner_id)
        
        hospital_data = partner_data.iloc[0]
        hospital_info = {
            'HOSPITAL': str(hospital_data['HOSPITAL']),
            'TIER': str(hospital_data['HOSP_TYPE']),
            'CATEGORY': 'Multi-Specialty',
            'ADDRESS': f"{str(hospital_data['CITY'])}, {str(hospital_data['STATE'])} - {str(hospital_data['PIN'])}",
            'INFRA_SCORE': round(float(np.random.uniform(3.5, 5.0)), 1)
        }
        
        response = {
            'hospital_info': hospital_info,
            **{key: handle_nan_values(value.to_dict(orient="records")) for key, value in results.items()}
        }
        
        return response
        
    except HTTPException as he:
        logger.error(f"HTTP Exception: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"Error processing claims analysis: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

# Mount static files with error handling
try:
    # Mount assets directory
    assets_dir = "../frontend/HospIntel/dist/assets"
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    else:
        logging.warning(f"Assets directory not found: {assets_dir}")

    # Mount images directory
    img_dir = "../frontend/HospIntel/public/img"
    if os.path.exists(img_dir):
        app.mount("/img", StaticFiles(directory=img_dir), name="img")
    else:
        logging.warning(f"Images directory not found: {img_dir}")

except Exception as e:
    logging.error(f"Error mounting static files: {e}")

# Add catch-all route for frontend app - this should be the last route
@app.get("/{full_path:path}")
async def serve_app(full_path: str):
    """Serve the frontend app for all routes, letting the client handle routing"""
    try:
        # Log the request
        logging.info(f"Serving frontend app for path: {full_path}")
        
        # Check if the path is an API route
        if full_path.startswith('api/'):
            raise HTTPException(status_code=404, detail="API route not found")
            
        # Serve the index.html for all other routes
        index_path = "../frontend/HospIntel/dist/index.html"
        if not os.path.exists(index_path):
            raise FileNotFoundError(f"Index file not found at: {index_path}")
            
        return FileResponse(index_path)
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error serving app: {e}")
        return JSONResponse(
            status_code=500,
            content={"message": "Error serving the application"}
        )

def handle_nan_values(obj):
    """Recursively handle NaN values in dictionaries and lists"""
    if isinstance(obj, dict):
        return {k: handle_nan_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [handle_nan_values(item) for item in obj]
    elif isinstance(obj, (float, np.float64, np.float32, np.float16)):
        if pd.isna(obj) or np.isnan(obj):
            return None
        return float(obj)
    elif isinstance(obj, (int, np.int64, np.int32, np.int16)):
        return int(obj)
    elif isinstance(obj, (str, np.character)):
        return str(obj)
    elif pd.isna(obj):
        return None
    return obj

if __name__ == '__main__':
    import uvicorn
    logging.info("Starting server on 0.0.0.0:5002")
    uvicorn.run(app, host="0.0.0.0", port=5002) 