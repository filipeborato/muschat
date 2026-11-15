;;; Muschat — approved patch "distribute_by_constraints" (authored by the Muschat project; calls Csolver,
;;; variable-domains, and generic-cnstr from Situation).
;;;
;;; Reads <folder>/entrada.txt: N, then for each slot the domain size followed by candidate pitches (midicents), and
;;; optionally P followed by P triples "i j d" (|pitch(i) - pitch(j)| /= d cents: forbidden interval).
;;; Each slot is a variable with its domain (variable-domains); a generic constraint requires distinct values across
;;; all slot combinations (no unisons, each pitch used at most once). Writes N pitches in slot order to
;;; <folder>/saida.txt, or -1 if the solver finds no solution. On error, writes -2 to saida.txt and message to erro.txt.
;;; Contains no Situation code: only calls public functions.
;;;
;;; OpenMusic usage: Lisp > Load File > this file, next to entrada.txt.

(in-package :om)

(load-lib-for '("Situation"))

(defun muschat-situation-read (filepath)
  "Integers only: *read-eval* disabled and anything else aborts."
  (let ((*read-eval* nil))
    (with-open-file (s filepath :direction :input)
      (loop for x = (read s nil :eof)
            until (eq x :eof)
            do (unless (integerp x) (error "entrada.txt contains a non-integer value: ~S" x))
            collect x))))

(defun muschat-situation-write (folder filename text)
  (with-open-file (s (merge-pathnames filename folder) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string text s)))

(defun muschat-situation-domains (numbers)
  "N m1 a b c m2 d e ... [P i j d ...] -> (values ((a b c) (d e) ...) ((i j d) ...))"
  (let* ((n (pop numbers))
         (domains (loop repeat n
                        collect (let ((m (pop numbers)))
                                  (when (< (length numbers) m) (error "entrada.txt ends before the declared domain size"))
                                  (loop repeat m collect (pop numbers)))))
         (p (if numbers (pop numbers) 0)))
    (unless (= (length numbers) (* 3 p)) (error "entrada.txt declares ~D pairs but provides ~D numbers" p (length numbers)))
    (values domains (loop repeat p collect (list (pop numbers) (pop numbers) (pop numbers))))))

(defun muschat-situation-pair (i j d)
  "|pitch(i) - pitch(j)| /= d for the given slot pair only (option f)."
  (generic-cnstr #'(lambda (a b) (/= (abs (- a b)) d)) (list i j) 'f))

(defun muschat-distribute-by-constraints (folder)
  (handler-case
      (multiple-value-bind (domains pairs)
          (muschat-situation-domains (muschat-situation-read (merge-pathnames "entrada.txt" folder)))
        (let* ((n (length domains))
               (slots (loop for i below n collect i))
               ;; generic-cnstr already returns a constraint list (one per slot pair): pass straight to Csolver
               (constraints (append (generic-cnstr #'(lambda (a b) (/= a b)) slots 'a)
                                    (loop for (i j d) in pairs append (muschat-situation-pair i j d))))
               (solution (csolver n nil nil nil constraints nil (variable-domains domains) nil 1))
               (values (remove-if-not #'numberp (flat solution))))
          (muschat-situation-write folder "saida.txt"
                                   (if values (format nil "~{~D~^ ~}~%" values) (format nil "-1~%")))
          values))
    (error (e)
      (muschat-situation-write folder "erro.txt" (format nil "~A~%" e))
      (muschat-situation-write folder "saida.txt" (format nil "-2~%")))))

;; The task folder follows the loaded file regardless of workspace location.
(muschat-distribute-by-constraints (make-pathname :name nil :type nil :defaults *load-truename*))
