import requests
import mysql.connector
import cv2
import RPi.GPIO as GPIO
import time
import os
import threading
from gpiozero import LED
from gpiozero.pins.pigpio import PiGPIOFactory
from ultralytics import YOLO
import numpy as np

# GPIO 設定
GPIO.setwarnings(False)  # 關閉 GPIO 警告
GPIO.setmode(GPIO.BCM)

# 超音波感測器引腳設置
TRIG1 = 23
ECHO1 = 24
TRIG2 = 5
ECHO2 = 6
GPIO.setup(TRIG1, GPIO.OUT)
GPIO.setup(ECHO1, GPIO.IN)
GPIO.setup(TRIG2, GPIO.OUT)
GPIO.setup(ECHO2, GPIO.IN)

# LED 設定
factory = PiGPIOFactory()
left_led = LED(17, pin_factory=factory)
right_led = LED(27, pin_factory=factory)
led3 = LED(22, pin_factory=factory)  # 新增的 LED3

# API 網址
API_URL = 'http://10.1.7.124:8080/classify'

# 影像處理
cap = cv2.VideoCapture(0)  # 使用攝像頭 0 (可以根據需要修改為 1 或其他設備)
if not cap.isOpened():
    print("無法打開攝像頭，請檢查設備")
    exit()

# YOLO 模型載入
model = YOLO("/home/tmp214/trashClass/best.pt")

# 儲存照片的目錄
save_path = "/home/tmp214/trashClass/save/"
if not os.path.exists(save_path):
    os.makedirs(save_path)

# 用來儲存攝像頭影像的全域變數
frame = None
lock = threading.Lock()  # 用來確保影像捕捉時不會發生資料競爭

# 開啟攝像頭並捕捉影像
def capture_video():
    global frame
    ret, new_frame = cap.read()
    if ret:
        with lock:
            frame = new_frame
    else:
        print("無法捕捉影像")

# 距離測量函式
def get_distance(TRIG, ECHO):
    GPIO.output(TRIG, GPIO.LOW)
    time.sleep(0.1)

    GPIO.output(TRIG, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(TRIG, GPIO.LOW)

    while GPIO.input(ECHO) == GPIO.LOW:
        pulse_start = time.time()

    while GPIO.input(ECHO) == GPIO.HIGH:
        pulse_end = time.time()

    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150  # 距離 (cm)
    return round(distance, 2)

# 回收物品判斷函式
def is_recyclable(class_name):
    recyclable_items = ['boba', 'lunchbox', 'milkbox', 'plastic', 'pouch']  # 排除 'paper cup'
    return class_name in recyclable_items

# 儲存並上傳影像
def save_and_upload_image(frame, item, save_path, item_name):
    category = "回收" if item == "recyclable" else "一般"
    current_time = time.localtime()  # 取得當前時間
    formatted_time = time.strftime("%Y-%m-%d_%H%M%S", current_time)  # 格式化時間為 'YYYY-MM-DD_HHMMSS'
    
    # 構造檔案名：category_物品名稱_日期時間.jpg
    filename = f"{category}_{item_name}_{formatted_time}.jpg"
    file_path = os.path.join(save_path, filename)

    # 儲存影像
    cv2.imwrite(file_path, frame)
    print(f"照片已儲存: {file_path}")

    # 上傳影像到 API
    with open(file_path, 'rb') as f:
        files = {'file': (filename, f, 'image/jpeg')}
        response = requests.post(API_URL, files=files)
        if response.status_code == 200:
            print("影像已成功上傳到 API")
        else:
            print(f"影像上傳失敗，HTTP 狀態碼: {response.status_code}")

# 偵測回收物品並儲存
def detect_and_process_image(frame, model):
    results = model.predict(frame, stream=True)
    classes_names = None
    detected_items = []  # 用來存儲檢測到的物品類別

    # 遍歷 YOLO 回傳的每個結果
    for result in results:
        classes_names = result.names
        for box in result.boxes:
            if box.conf[0] > 0.4:  # 設定最低置信度
                cls = int(box.cls[0])
                class_name = classes_names[cls]
                detected_items.append(class_name)

    for item in detected_items:
        if is_recyclable(item):
            save_and_upload_image(frame, "recyclable", save_path, item)
            return "recyclable", item
        elif item == "other":
            save_and_upload_image(frame, "other", save_path, item)
            return "other", item

    return None, None

# 顯示影像並等待 3 秒後關閉攝像機
def show_and_close_camera(frame):
    cv2.imshow('Captured Image', frame)
    cv2.waitKey(3000)  # 顯示 3 秒
    cap.release()  # 關閉攝像頭
    cv2.destroyAllWindows()  # 關閉影像視窗

# 連接到 MySQL 資料庫並儲存資料
def save_to_mysql(class_name, recycle):
    try:
        # 嘗試建立 MySQL 連線
        conn = mysql.connector.connect(
            host="database-1.cnwykisoqb94.ap-northeast-1.rds.amazonaws.com",  # 你的雲端 MySQL 端點
            port=3306,  # MySQL 默認端口
            user="admin",  # 你的 MySQL 使用者名稱
            password="#Tibame01",  # 你的 MySQL 密碼
            database="trash_2"  # 資料庫名稱
        )
        cursor = conn.cursor(dictionary=True)
        if class_name == 'lunchbox':
          class_name = "不可回收"

        # 插入資料到 trash_class 表格
        insert_sql = """
            INSERT INTO img (trash_category, identify_time)
            VALUES (%s, NOW())
        """
        cursor.execute(insert_sql, (class_name,))

        # 提交事務
        conn.commit()
        print("資料已成功儲存到 img 表格")

    except mysql.connector.Error as err:
        print(f"MySQL 錯誤: {err}")
        conn.rollback()  # 發生錯誤時回滾事務
    finally:
        cursor.close()
        conn.close()

        # 提交事務
        #conn.commit()
        print("資料已成功儲存到資料庫")

  
  
# 主程式
def main():
    try:
        while True:
            # 測量距離 (感測器1 和感測器2)
            distance1 = get_distance(TRIG1, ECHO1)
            distance2 = get_distance(TRIG2, ECHO2)

            # 如果感測器2距離小於 10 cm，常亮 LED3
            if distance2 < 10:
                led3.on()
            else:
                led3.off()

            # 如果感測器1距離小於 60 cm，啟動攝像頭進行拍攝
            if distance1 < 60:
                print("開啟攝像頭")

                # 讀取攝像頭影像並進行回收物品偵測
                capture_video()
                with lock:
                    if frame is not None:
                        # 進行回收物品偵測
                        item_type, item_name = detect_and_process_image(frame, model)
                        if item_type == "recyclable":
                            left_led.on()
                            save_to_mysql(item_name, True)  # 儲存到資料庫
                        elif item_type == "other":
                            right_led.on()
                            save_to_mysql(item_name, False)  # 儲存到資料庫

                        # 顯示並儲存影像
                        show_and_close_camera(frame)

            else:
                # 顯示感測器距離
                print(f"感測器1距離: {distance1} cm")
                time.sleep(0.5)  # 延遲 0.5 秒
                print(f"感測器2距離: {distance2} cm")
                time.sleep(0.5)  # 延遲 0.5 秒

            # 按下 'q' 鍵退出程式
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("程式已手動停止")

    finally:
        left_led.off()
        right_led.off()
        led3.off()
        GPIO.cleanup()

if __name__ == "__main__":
    # 執行主程式
    main()
