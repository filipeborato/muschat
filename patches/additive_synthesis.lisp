;;; Muschat — approved patch "additive_synthesis" (authored by the Muschat project; calls om-make-array with the
;;; OMChroma add-1 class and synthesize with :run nil).
;;;
;;; Reads <folder>/entrada.txt: N, followed by N lines "onset(s) duration(s) frequency(Hz) amplitude(dB)". Builds an
;;; add-1 event with N components, asks OMChroma to write only the Csound orchestra and score (:run nil, without invoking
;;; Csound), and copies both files to <folder>/muschat.orc and <folder>/muschat.sco. Writes N to <folder>/saida.txt.
;;; Audio is rendered afterwards by Muschat using the detected Csound binary. On error, writes -2 to saida.txt and the
;;; error message to erro.txt. Contains no OMChroma code: only calls public functions.
;;;
;;; OpenMusic usage: Lisp > Load File > this file, next to entrada.txt.

(in-package :om)

(load-lib-for '("OMChroma"))

(defun muschat-chroma-read (filepath)
  "Numbers only: *read-eval* disabled, floats read as double-float, anything else aborts."
  (let ((*read-eval* nil) (*read-default-float-format* 'double-float))
    (with-open-file (s filepath :direction :input)
      (loop for x = (read s nil :eof)
            until (eq x :eof)
            do (unless (numberp x) (error "entrada.txt contains a non-numeric value: ~S" x))
            collect x))))

(defun muschat-chroma-write (folder filename text)
  (with-open-file (s (merge-pathnames filename folder) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string text s)))

(defun muschat-chroma-copy (src dst)
  (with-open-file (in src :direction :input)
    (with-open-file (out dst :direction :output :if-exists :supersede :if-does-not-exist :create)
      (loop for line = (read-line in nil nil)
            while line do (write-line line out)))))

(defun muschat-additive-synthesis (folder)
  (handler-case
      (let* ((numbers (muschat-chroma-read (merge-pathnames "entrada.txt" folder)))
             (n (round (pop numbers))))
        (unless (= (length numbers) (* 4 n))
          (error "entrada.txt declares ~D components but provides ~D numbers" n (length numbers)))
        (let* ((rows (loop for rest on numbers by #'cddddr collect (subseq rest 0 4)))
               (event (om-make-array 'add-1 n 0 nil
                                     :e-dels (mapcar #'first rows)
                                     :durs (mapcar #'second rows)
                                     :freq (mapcar #'third rows)
                                     :amp (mapcar #'fourth rows)))
               (files (synthesize event :name "muschat" :run nil)))
          (unless (and (first files) (second files))
            (error "OMChroma did not return .orc/.sco files: ~S" files))
          (muschat-chroma-copy (first files) (merge-pathnames "muschat.orc" folder))
          (muschat-chroma-copy (second files) (merge-pathnames "muschat.sco" folder))
          (muschat-chroma-write folder "saida.txt" (format nil "~D~%" n))
          n))
    (error (e)
      (muschat-chroma-write folder "erro.txt" (format nil "~A~%" e))
      (muschat-chroma-write folder "saida.txt" (format nil "-2~%")))))

;; The task folder follows the loaded file regardless of workspace location.
(muschat-additive-synthesis (make-pathname :name nil :type nil :defaults *load-truename*))
