import numpy as np
import os
import time
import argparse
import logging

import cv2
import pycuda.autoinit  # This is needed for initializing CUDA driver
import onnxruntime


from utils.yolo_classes import get_cls_dict
from utils.camera import add_camera_args, Camera
from utils.display import set_display, show_fps
from utils.visualization import BBoxVisualization
from utils.yolo_with_plugins import TrtYOLO

from Controller import *
from Motor import *

session_lane = onnxruntime.InferenceSession('../model/Pretrain_3.onnx', None, providers=['CUDAExecutionProvider'])
input_name_lane = session_lane.get_inputs()[0].name

session_sign = onnxruntime.InferenceSession('../model/sign_cnn.onnx', None, providers=['CPUExecutionProvider'])
input_name_sign = session_sign.get_inputs()[0].name

Motor = Motor_DC()
Servo = Motor_Servo()
def gstreamer_pipeline(
    sensor_id=0,
    capture_width=640,
    capture_height=360,
    display_width=640,
    display_height=360,
    framerate=30,
    flip_method=0,
):
    return (
        "nvarguscamerasrc sensor-id=%d !"
        "video/x-raw(memory:NVMM), width=(int)%d, height=(int)%d, framerate=(fraction)%d/1 ! "
        "nvvidconv flip-method=%d ! "
        "video/x-raw, width=(int)%d, height=(int)%d, format=(string)BGRx ! "
        "videoconvert ! "
        "video/x-raw, format=(string)BGR ! appsink"
        % (
            sensor_id,
            capture_width,
            capture_height,
            framerate,
            flip_method,
            display_width,
            display_height,
        )
    )
def remove_small_contours(image):
    try:
        image_binary = np.zeros((image.shape[0], image.shape[1]), np.uint8)
        contours = cv2.findContours(image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]
        mask = cv2.drawContours(image_binary, [max(contours, key=cv2.contourArea)], -1, (255, 255, 255), -1)
        image_remove = cv2.bitwise_and(image, image, mask=mask)
        return image_remove
    except Exception as e:
        logging.error(e)
        return image
        

def road_lines(image, session, inputname):
	# Crop ảnh lại, lấy phần ảnh có làn đườngs
	image = image[200:, :, :]
	small_img = cv2.resize(image, ((image.shape[1]//4, image.shape[0]//4)))
	cv2.imshow('image',small_img)
	small_img = small_img/255
	small_img = np.array(small_img, dtype=np.float32)
	small_img = small_img[None, :, :, :]
	prediction = session.run(None, {inputname: small_img})
	prediction = np.squeeze(prediction)
	prediction = np.where(prediction < 0.5, 0, 255)
	prediction = prediction.astype(np.uint8)

	return prediction

def Classify(img,inputname,session):
    img = cv2.resize(img,(30, 30))
    img = img.astype('float32')/255
    img = img.reshape(1,30,30,3)
    
    prediction = session.run(None,{inputname:img})
    prediction = np.squeeze(prediction) 
    cll = np.argmax(prediction)

    return cll
def Get_name(predictions):
    if str(predictions) == "0":
        name_class = "Left"
    if str(predictions) == "1":
        name_class = "Right"
    if str(predictions) == "2":
        name_class = "Stop"
    if str(predictions) == "3":
        name_class = "Straight"
    return name_class
   
def parse_args():
    """Parse input arguments."""
    desc = ('Capture and display live camera video, while doing '
            'real-time object detection with TensorRT optimized '
            'YOLO model on Jetson')
    parser = argparse.ArgumentParser(description=desc)
    # parser = add_camera_args(parser)
    parser.add_argument(
        '-c', '--category_num', type=int, default=4,
        help='number of object categories [80]')
    parser.add_argument(
        '-t', '--conf_thresh', type=float, default=0.3,
        help='set the detection confidence threshold')
    parser.add_argument(
        '-m', '--model', type=str, required=True,
        help=('[yolov3-tiny|yolov3|yolov3-spp|yolov4-tiny|yolov4|'
              'yolov4-csp|yolov4x-mish|yolov4-p5]-[{dimension}], where '
              '{dimension} could be either a single number (e.g. '
              '288, 416, 608) or 2 numbers, WxH (e.g. 416x256)'))
    parser.add_argument(
        '-l', '--letter_box', action='store_true',
        help='inference with letterboxed image [False]')
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    print("Category: ",args.category_num)
    if args.category_num <= 0:
        raise SystemExit('ERROR: bad category_num (%d)!' % args.category_num)
    if not os.path.isfile('yolo/%s.trt' % args.model):
        raise SystemExit('ERROR: file (yolo/%s.trt) not found!' % args.model)


    # cls_dict = get_cls_dict(args.category_num)
    # vis = BBoxVisualization(cls_dict)
    trt_yolo = TrtYOLO(args.model, args.category_num, args.letter_box)
    cam = cv2.VideoCapture(gstreamer_pipeline(flip_method=0), cv2.CAP_GSTREAMER)
    if cam.isOpened():
        try:
            full_scrn = False
            fps = 0.0
            tic = time.time()
            while True:
                tic = time.time()
                ret, img = cam.read()
                copy_image = np.copy(img)
                segmentation = road_lines(copy_image, session=session_lane, inputname=input_name_lane)
                segmentation = remove_small_contours(segmentation)

                controller = Controller(segmentation)
                angle, speed = controller()

                Servo.Rotate_angle(0,angle)
                Motor.setSpeed_pwm(speed)

                boxes, confs, clss = trt_yolo.detect(img, args.conf_thresh)
                if len(boxes) != 0:
                    x_min = int(boxes[0][0])
                    y_min = int(boxes[0][1])
                    x_max = int(boxes[0][2])
                    y_max = int(boxes[0][3])
                    
                    cls_img = img[y_min:y_max,x_min:x_max]
                    cll = Classify(cls_img,input_name_sign,session_sign)
                    name_class = Get_name(cll)

                    cv2.rectangle(img,(x_min,y_min),(x_max,y_max),(0,0,255),2)
                    cv2.putText(img,name_class, (x_min,y_min-10),cv2.FONT_HERSHEY_COMPLEX, 0.5, (255, 255, 255), 1)
                    cv2.putText(img,str(np.round(confs[0],2)), (x_min+len(name_class)*2+40,y_min-10),cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 255, 255), 1)
                else:
                    cll = "None"

                img = show_fps(img, fps)
                toc = time.time()
                curr_fps = 1.0 / (toc - tic)
                # calculate an exponentially decaying average of fps number
                fps = curr_fps if fps == 0.0 else (fps*0.95 + curr_fps*0.05)
                tic = toc

                cv2.imshow("Show main image ",img)
                cv2.imshow("segmentation ",segmentation)
                keyCode = cv2.waitKey(10) & 0xFF
                if keyCode == 27 or keyCode == ord('q'):
                    Servo.Rotate_angle(0,90)
                    Motor.setSpeed_pwm(0)
                    time.sleep(2)
                    break
        finally:
            cam.release()
            cv2.destroyAllWindows()
    else:
        print("Camera is error")

if __name__ == '__main__':
    main()
