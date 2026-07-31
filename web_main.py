import os
import sys
import logging

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    from common.config import load_config
    from web.app import create_web_app
    
    config = load_config()
    app = create_web_app(config)
    
    port = config.get('server', {}).get('web_port', 8990)
    
    print(f"==================================================")
    print(f"  云印宝 - Web管理后台")
    print(f"  访问地址: http://localhost:{port}")
    print(f"  默认账号: admin / admin123")
    print(f"==================================================")
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()