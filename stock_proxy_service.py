#!/usr/bin/env python3
"""
Stock Proxy Service
Runs on host system to provide Yahoo Finance data to Docker containers
This bypasses Docker networking issues with Yahoo Finance
"""

from flask import Flask, jsonify, request
import sys
import os
import logging

# Add the current directory to Python path to import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api_clients.yahoo_finance_client import get_stock_price, get_daily_time_series, get_intraday_time_series

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "service": "stock_proxy"})

@app.route('/stock/<symbol>', methods=['GET'])
def get_stock_data(symbol):
    """Get stock price data for a symbol"""
    try:
        logger.info(f"Stock proxy request for symbol: {symbol}")
        data = get_stock_price(symbol)
        
        if data:
            logger.info(f"Successfully retrieved data for {symbol}")
            return jsonify(data)
        else:
            logger.warning(f"No data found for {symbol}")
            return jsonify({"error": "No data found"}), 404
            
    except Exception as e:
        logger.error(f"Error getting stock data for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/stock/<symbol>/daily', methods=['GET'])
def get_daily_data(symbol):
    """Get daily time series data for a symbol"""
    try:
        outputsize = request.args.get('outputsize', 'compact')
        logger.info(f"Daily data request for symbol: {symbol}, outputsize: {outputsize}")
        
        data = get_daily_time_series(symbol, outputsize)
        
        if data:
            logger.info(f"Successfully retrieved daily data for {symbol}: {len(data)} points")
            return jsonify({"symbol": symbol, "data": data})
        else:
            logger.warning(f"No daily data found for {symbol}")
            return jsonify({"error": "No data found"}), 404
            
    except Exception as e:
        logger.error(f"Error getting daily data for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/stock/<symbol>/intraday', methods=['GET'])
def get_intraday_data(symbol):
    """Get intraday time series data for a symbol"""
    try:
        interval = request.args.get('interval', '60min')
        outputsize = request.args.get('outputsize', 'compact')
        logger.info(f"Intraday data request for symbol: {symbol}, interval: {interval}")
        
        data = get_intraday_time_series(symbol, interval, outputsize)
        
        if data:
            logger.info(f"Successfully retrieved intraday data for {symbol}: {len(data)} points")
            return jsonify({"symbol": symbol, "interval": interval, "data": data})
        else:
            logger.warning(f"No intraday data found for {symbol}")
            return jsonify({"error": "No data found"}), 404
            
    except Exception as e:
        logger.error(f"Error getting intraday data for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🏭 Starting Stock Proxy Service...")
    print("📊 This service provides Yahoo Finance data to Docker containers")
    print("🌐 Available endpoints:")
    print("   GET /health - Health check")
    print("   GET /stock/<symbol> - Get current stock price")
    print("   GET /stock/<symbol>/daily?outputsize=compact - Get daily data")
    print("   GET /stock/<symbol>/intraday?interval=60min - Get intraday data")
    print("")
    print("🚀 Starting server on http://localhost:9999")
    print("   Container access: http://host.docker.internal:9999")
    print("")
    
    app.run(host='0.0.0.0', port=9999, debug=False) 