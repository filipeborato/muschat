;;; Muschat — approved patch "chaos_verhulst" (chaotic density, register, dynamics, and durations; authored by the
;;; Muschat project; calls Verhulst from Chaos).
;;;
;;; Reads <folder>/entrada.txt (three numbers: x0 lambda iterations), calls (alea::verhulst x0 lambda iterations),
;;; which returns iterations + 1 values, and writes the series to <folder>/saida.txt separated by spaces. On error,
;;; writes -2 to saida.txt and the error message to erro.txt. Contains no Chaos code: only calls the public function.
;;;
;;; OpenMusic usage: Lisp > Load File > this file, next to entrada.txt.

(in-package :om)

(load-lib-for '("Chaos"))

(defun muschat-chaos-read (filepath)
  "Numbers only: *read-eval* disabled, floats read as double-float, anything else aborts."
  (let ((*read-eval* nil) (*read-default-float-format* 'double-float))
    (with-open-file (s filepath :direction :input)
      (loop for x = (read s nil :eof)
            until (eq x :eof)
            do (unless (numberp x) (error "entrada.txt contains a non-numeric value: ~S" x))
            collect x))))

(defun muschat-chaos-write (folder filename text)
  (with-open-file (s (merge-pathnames filename folder) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string text s)))

(defun muschat-chaos-verhulst (folder)
  (handler-case
      (let ((numbers (muschat-chaos-read (merge-pathnames "entrada.txt" folder))))
        (unless (= (length numbers) 3)
          (error "entrada.txt requires 3 numbers, got ~D" (length numbers)))
        (destructuring-bind (x0 lam iterations) numbers
          (let ((series (alea::verhulst (float x0 1d0) (float lam 1d0) (round iterations))))
            (muschat-chaos-write folder "saida.txt" (format nil "~{~,10F~^ ~}~%" series))
            series)))
    (error (e)
      (muschat-chaos-write folder "erro.txt" (format nil "~A~%" e))
      (muschat-chaos-write folder "saida.txt" (format nil "-2~%")))))

;; The task folder follows the loaded file regardless of workspace location.
(muschat-chaos-verhulst (make-pathname :name nil :type nil :defaults *load-truename*))
