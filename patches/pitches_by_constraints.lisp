;;; Muschat — approved patch "pitches_by_constraints" (authored by the Muschat project; calls ClusterEngine and the
;;; rules R-pitches-one-voice, R-pitch-pitch, and HR-pitches-one-voice from Cluster Engine by Örjan Sandred,
;;; ported to OpenMusic by Paulo Raposo).
;;;
;;; Reads <folder>/entrada.txt (integers only):
;;;   V S U C H M        voices, max leap (cents), no-unison & no-crossing (0 off, 1 preference, 2 strict),
;;;                      common tones heuristic (0/1), solver variable count
;;;   NUM DEN            time signature
;;;   per voice: E n1 d1 n2 d2 ...   fixed rhythm (E events; n/d of a whole note; negative n = rest)
;;;              D a1 a2 ...         candidate pitches for the voice (midicents)
;;; Each voice's rhythm enters as a single-motif domain (locked); the solver chooses pitches only. Voices are ordered
;;; top to bottom (score part order), which "no crossing" assumes.
;;; Writes <folder>/saida.txt: V and per voice k followed by k note pitches in order; -1 if no solution exists.
;;; On error, writes -2 to saida.txt and the error message to erro.txt. Contains no Cluster Engine code: calls only.
;;;
;;; OpenMusic usage: Lisp > Load File > this file, next to entrada.txt.

(in-package :om)

(load-lib-for '("Cluster-Engine"))

(defun muschat-ce-read (filepath)
  "Integers only: *read-eval* disabled and anything else aborts."
  (let ((*read-eval* nil))
    (with-open-file (s filepath :direction :input)
      (loop for x = (read s nil :eof)
            until (eq x :eof)
            do (unless (integerp x) (error "entrada.txt contains a non-integer value: ~S" x))
            collect x))))

(defun muschat-ce-write (folder filename text)
  (with-open-file (s (merge-pathnames filename folder) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string text s)))

(defun muschat-ce-voices (numbers v)
  "-> list of (rhythm domain); trailing numbers raise an inconsistent-input error."
  (let ((voices (loop repeat v
                      collect (let* ((e (pop numbers))
                                     (rhythm (loop repeat e collect (let ((n (pop numbers)) (d (pop numbers))) (/ n d))))
                                     (d (pop numbers)))
                                (when (< (length numbers) d) (error "entrada.txt ends before the declared domain size"))
                                (list rhythm (loop repeat d collect (pop numbers)))))))
    (when numbers (error "entrada.txt has ~D trailing numbers" (length numbers)))
    voices))

(defun muschat-ce-onsets (rhythm)
  (count-if #'plusp rhythm))

;; Rules are named global functions, never lambdas: Cluster Engine compiles every rule, and LispWorks does not
;; compile lexical closures ("... is a lexical closure", observed in real OM). Max leap passes via special var.
(defvar *muschat-ce-max-leap* 1200)

;; Search deadline: Cluster Engine has no built-in time limit and large searches freeze OpenMusic (observed in real
;; tests). Every rule checks the clock; past the deadline the error aborts search and produces -2 + erro.txt.
(defvar *muschat-ce-deadline* 0)
(defparameter *muschat-ce-timeout-seconds* 60)

(defun muschat-ce-check-clock ()
  (when (> (get-internal-real-time) *muschat-ce-deadline*)
    (error "Cluster Engine exceeded ~D s without finding a solution; shorten the excerpt or disable a constraint"
           *muschat-ce-timeout-seconds*)))

(defun muschat-ce-leap-ok (a b)
  (muschat-ce-check-clock)
  (<= (abs (- b a)) *muschat-ce-max-leap*))

(defun muschat-ce-repeat (a b)
  "Common-tones heuristic: weight 1 when the pitch repeats."
  (muschat-ce-check-clock)
  (if (= a b) 1 0))

(defun muschat-ce-descending (pitches)
  "No crossing: voices run top to bottom; rests (nil) are ignored."
  (muschat-ce-check-clock)
  (let ((sounding (remove nil pitches)))
    (or (null sounding) (apply #'>= sounding))))

(defun muschat-ce-no-unison (pitches)
  (muschat-ce-check-clock)
  (let ((sounding (remove nil pitches)))
    (= (length sounding) (length (remove-duplicates sounding)))))

(defun muschat-ce-between-voices (level rule all-voices)
  "1 = preference (heuristic ordering candidates, never fails); 2 = strict. Strict voice-leading constraints stall the
solver when rhythms are locked (top voice gets trapped with no pitch above lower voices without backtracking locked
rhythm engines), so preference is the default."
  (case level
    (1 (cluster-engine::r-pitch-pitch rule all-voices '(0) :all :exclude-gracenotes :pitch :heur-switch 10))
    (2 (cluster-engine::r-pitch-pitch rule all-voices '(0) :all :exclude-gracenotes :pitch))))

(defun muschat-pitches-by-constraints (folder)
  (handler-case
      (let* ((numbers (muschat-ce-read (merge-pathnames "entrada.txt" folder)))
             (v (pop numbers)) (max-leap (pop numbers)) (unison (pop numbers)) (crossing (pop numbers))
             (common-tones (pop numbers)) (variables (pop numbers))
             (meter (list (pop numbers) (pop numbers)))
             (voices (muschat-ce-voices numbers v))
             (domains (loop for (rhythm domain) in voices
                            append (list (list rhythm) (mapcar #'list domain))))
             (all-voices (loop for i below v collect i))
             (*muschat-ce-max-leap* max-leap)
             (*muschat-ce-deadline* (+ (get-internal-real-time)
                                       (* *muschat-ce-timeout-seconds* internal-time-units-per-second)))
             (rules
               (append
                ;; each rule helper returns a list of rules; final list must be flat (filter-rules-from-input)
                (cluster-engine::r-pitches-one-voice #'muschat-ce-leap-ok all-voices :pitches)
                (when (> v 1) (muschat-ce-between-voices unison #'muschat-ce-no-unison all-voices))
                (when (> v 1) (muschat-ce-between-voices crossing #'muschat-ce-descending all-voices))
                (when (= common-tones 1)
                  (cluster-engine::hr-pitches-one-voice #'muschat-ce-repeat all-voices :pitches))))
             ;; rnd? = t: otherwise the solver always picks the first candidate (lowest pitches)
             ;; shortcut: single meter (from bar 1); no rule reads meter, extend if any future rule inspects meter
             (result (cluster-engine::clusterengine variables t nil rules (list meter) domains))
             ;; when unsolvable, solver returns a partial solution (non-nil) missing pitches in some voice
             (pitches (loop for i below v collect (remove nil (flat (nth (1+ (* 2 i)) result)))))
             (complete (loop for (rhythm nil) in voices for p in pitches
                             always (>= (length p) (muschat-ce-onsets rhythm)))))
        (muschat-ce-write
         folder "saida.txt"
         (if complete
             (format nil "~D~{ ~D~{ ~D~}~}~%" v (loop for p in pitches append (list (length p) p)))
             (format nil "-1~%")))
        result)
    (error (e)
      (muschat-ce-write folder "erro.txt" (format nil "~A~%" e))
      (muschat-ce-write folder "saida.txt" (format nil "-2~%")))))

;; The task folder follows the loaded file regardless of workspace location.
(muschat-pitches-by-constraints (make-pathname :name nil :type nil :defaults *load-truename*))
