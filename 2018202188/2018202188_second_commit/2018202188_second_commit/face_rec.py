import numpy as np
import cv2


face_cascade = cv2.CascadeClassifier('face1.xml')
eye_cascade = cv2.CascadeClassifier('eye.xml')

cap = cv2.VideoCapture(0)#开镜

while 1:
    ret, img = cap.read()
    img = cv2.flip(img, 1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x,y,w,h) in faces:
        cv2.rectangle(img,(x,y),(x+w,y+h),(255,0,0),2)
        roi_gray = gray[y:y+h, x:x+w]
        roi_color = img[y:y+h, x:x+w]
        print (int(x+w/2), int(y+h/2))
        eyes = eye_cascade.detectMultiScale(roi_gray)
        for (ex,ey,ew,eh) in eyes:
            cv2.rectangle(roi_color,(ex,ey),(ex+ew,ey+eh),(0,255,0),2)
    #imgR = cv2.flip(img, 1)
    cv2.imshow('img',img)
    k = cv2.waitKey(30) & 0xff
    if k == 27: #esc
        break

cap.release()
cv2.destroyAllWindows()
