;;; Muschat — patch aprovado "serie_harmonica" (autoria do projeto Muschat; chama harm-series da Esquisse).
;;;
;;; Lê <pasta>/entrada.txt (cinco números: fundamental_em_midicents numer denom begin end), chama
;;; (harm-series fund numer denom begin end 1 1) com unit = Midics e type = Chord, e grava a lista de midicents em
;;; <pasta>/saida.txt, separados por espaço. Não contém código da Esquisse (GPL): só a chamada à função pública.
;;;
;;; Uso no OpenMusic: Lisp > Load File > este arquivo, ao lado de entrada.txt.

(in-package :om)

(load-lib-for '("esquisse"))

(defun muschat-ler-entrada (arquivo)
  "Só números: *read-eval* desligado e qualquer coisa que não seja número interrompe."
  (let ((*read-eval* nil))
    (with-open-file (s arquivo :direction :input)
      (loop for x = (read s nil :fim)
            until (eq x :fim)
            do (unless (numberp x) (error "entrada.txt com algo que não é número: ~S" x))
            collect x))))

(defun muschat-serie-harmonica (pasta)
  (let* ((numeros (muschat-ler-entrada (merge-pathnames "entrada.txt" pasta))))
    (unless (= (length numeros) 5)
      (error "entrada.txt precisa de 5 números, tem ~D" (length numeros)))
    (destructuring-bind (fund numer denom begin end) numeros
      (let ((serie (harm-series fund numer denom begin end 1 1)))
        (with-open-file (s (merge-pathnames "saida.txt" pasta)
                           :direction :output :if-exists :supersede :if-does-not-exist :create)
          (format s "~{~D~^ ~}~%" (mapcar #'round (flat (list serie)))))
        serie))))

;; A pasta da tarefa acompanha o arquivo carregado, independentemente do workspace.
(muschat-serie-harmonica (make-pathname :name nil :type nil :defaults *load-truename*))
