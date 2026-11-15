;;; Muschat — approved patch "harmonic_series" (authored by the Muschat project; calls Esquisse harm-series).
;;;
;;; Reads <folder>/entrada.txt (five numbers: fundamental_midicents numer denom begin end), calls
;;; (harm-series fund numer denom begin end 1 1) with unit = Midics and type = Chord, and writes the midicents list
;;; to <folder>/saida.txt separated by spaces. Contains no Esquisse (GPL) code: only calls the public function.
;;;
;;; OpenMusic usage: Lisp > Load File > this file, next to entrada.txt.

(in-package :om)

(load-lib-for '("esquisse"))

(defun muschat-read-input (filepath)
  "Numbers only: *read-eval* disabled and any non-numeric token stops execution."
  (let ((*read-eval* nil))
    (with-open-file (s filepath :direction :input)
      (loop for x = (read s nil :eof)
            until (eq x :eof)
            do (unless (numberp x) (error "entrada.txt contains a non-numeric value: ~S" x))
            collect x))))

(defun muschat-harmonic-series (folder)
  (let* ((numbers (muschat-read-input (merge-pathnames "entrada.txt" folder))))
    (unless (= (length numbers) 5)
      (error "entrada.txt requires 5 numbers, got ~D" (length numbers)))
    (destructuring-bind (fund numer denom begin end) numbers
      (let ((series (harm-series fund numer denom begin end 1 1)))
        (with-open-file (s (merge-pathnames "saida.txt" folder)
                           :direction :output :if-exists :supersede :if-does-not-exist :create)
          (format s "~{~D~^ ~}~%" (mapcar #'round (flat (list series)))))
        series))))

;; The task folder follows the loaded file regardless of workspace location.
(muschat-harmonic-series (make-pathname :name nil :type nil :defaults *load-truename*))
