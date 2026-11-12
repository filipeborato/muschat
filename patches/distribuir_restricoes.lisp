;;; Muschat — patch aprovado "distribuir_restricoes" (autoria do projeto Muschat; chama Csolver, variable-domains e
;;; generic-cnstr da Situation).
;;;
;;; Lê <pasta>/entrada.txt: N, depois, para cada vaga, o tamanho do domínio seguido das alturas possíveis (midicents).
;;; Cada vaga é uma variável com o seu domínio (variable-domains); uma restrição genérica exige valores diferentes em
;;; todas as combinações de vagas (sem uníssono, cada altura uma vez). Grava em <pasta>/saida.txt as N alturas na
;;; ordem das vagas, ou -1 se o solver não achar solução. Em erro, -2 em saida.txt e a mensagem em erro.txt.
;;; Não contém código da Situation: só chamadas às funções públicas.
;;;
;;; Uso no OpenMusic: Lisp > Load File > este arquivo, ao lado de entrada.txt.

(in-package :om)

(load-lib-for '("Situation"))

(defun muschat-situation-ler (arquivo)
  "Só números inteiros: *read-eval* desligado e qualquer outra coisa interrompe."
  (let ((*read-eval* nil))
    (with-open-file (s arquivo :direction :input)
      (loop for x = (read s nil :fim)
            until (eq x :fim)
            do (unless (integerp x) (error "entrada.txt com algo que não é inteiro: ~S" x))
            collect x))))

(defun muschat-situation-gravar (pasta nome texto)
  (with-open-file (s (merge-pathnames nome pasta) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string texto s)))

(defun muschat-situation-dominios (numeros)
  "N m1 a b c m2 d e ... -> ((a b c) (d e) ...)"
  (let ((n (pop numeros)))
    (loop repeat n
          collect (let ((m (pop numeros)))
                    (when (< (length numeros) m) (error "entrada.txt termina antes do domínio anunciado"))
                    (loop repeat m collect (pop numeros))))))

(defun muschat-distribuir-restricoes (pasta)
  (handler-case
      (let* ((dominios (muschat-situation-dominios (muschat-situation-ler (merge-pathnames "entrada.txt" pasta))))
             (n (length dominios))
             (vagas (loop for i below n collect i))
             (diferentes (generic-cnstr #'(lambda (a b) (/= a b)) vagas 'a))
             (solucao (csolver n nil nil nil (list diferentes) nil (variable-domains dominios) nil 1))
             (valores (remove-if-not #'numberp (flat solucao))))
        (muschat-situation-gravar pasta "saida.txt"
                                  (if valores (format nil "~{~D~^ ~}~%" valores) (format nil "-1~%")))
        valores)
    (error (e)
      (muschat-situation-gravar pasta "erro.txt" (format nil "~A~%" e))
      (muschat-situation-gravar pasta "saida.txt" (format nil "-2~%")))))

;; A pasta da tarefa acompanha o arquivo carregado, independentemente do workspace.
(muschat-distribuir-restricoes (make-pathname :name nil :type nil :defaults *load-truename*))
