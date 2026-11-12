;;; Muschat — patch aprovado "densidade_caotica" (autoria do projeto Muschat; chama Verhulst da Chaos).
;;;
;;; Lê <pasta>/entrada.txt (três números: x0 lambda iteracoes), chama (alea::verhulst x0 lambda iteracoes), que
;;; devolve iteracoes + 1 valores, e grava a série em <pasta>/saida.txt, separados por espaço. Em erro, grava -2 em
;;; saida.txt e a mensagem em erro.txt. Não contém código da Chaos: só a chamada à função pública.
;;;
;;; Uso no OpenMusic: Lisp > Load File > este arquivo, ao lado de entrada.txt.

(in-package :om)

(load-lib-for '("Chaos"))

(defun muschat-chaos-ler (arquivo)
  "Só números: *read-eval* desligado, reais lidos em dupla precisão, qualquer outra coisa interrompe."
  (let ((*read-eval* nil) (*read-default-float-format* 'double-float))
    (with-open-file (s arquivo :direction :input)
      (loop for x = (read s nil :fim)
            until (eq x :fim)
            do (unless (numberp x) (error "entrada.txt com algo que não é número: ~S" x))
            collect x))))

(defun muschat-chaos-gravar (pasta nome texto)
  (with-open-file (s (merge-pathnames nome pasta) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string texto s)))

(defun muschat-densidade-caotica (pasta)
  (handler-case
      (let ((numeros (muschat-chaos-ler (merge-pathnames "entrada.txt" pasta))))
        (unless (= (length numeros) 3)
          (error "entrada.txt precisa de 3 números, tem ~D" (length numeros)))
        (destructuring-bind (x0 lam iteracoes) numeros
          (let ((serie (alea::verhulst (float x0 1d0) (float lam 1d0) (round iteracoes))))
            (muschat-chaos-gravar pasta "saida.txt" (format nil "~{~,10F~^ ~}~%" serie))
            serie)))
    (error (e)
      (muschat-chaos-gravar pasta "erro.txt" (format nil "~A~%" e))
      (muschat-chaos-gravar pasta "saida.txt" (format nil "-2~%")))))

;; A pasta da tarefa acompanha o arquivo carregado, independentemente do workspace.
(muschat-densidade-caotica (make-pathname :name nil :type nil :defaults *load-truename*))
