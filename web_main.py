import os, sys, logging
def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    from common.config import load_config
    from web.app import create_web_app
    cfg = load_config()
    app = create_web_app(cfg)
    port = cfg.get('server', {}).get('web_port', 8990)
    print('Web管理后台 http://localhost:' + str(port) + '  admin/admin123')
    app.run(host='0.0.0.0', port=port, debug=False)
if __name__ == '__main__': main()
