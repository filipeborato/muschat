;;; Muschat — patch aprovado "sintese_aditiva" (autoria do projeto Muschat; chama om-make-array com a classe add-1
;;; do OMChroma e synthesize com :run nil).
;;;
;;; Lê <pasta>/entrada.txt: N, depois N linhas "início(s) duração(s) frequência(Hz) amplitude(dB)". Cria um evento
;;; add-1 com N componentes, pede ao OMChroma só a escrita da orquestra e da partitura Csound (:run nil, sem chamar o
;;; Csound) e copia os dois arquivos para <pasta>/muschat.orc e <pasta>/muschat.sco. Grava N em <pasta>/saida.txt.
;;; O áudio é renderizado depois pelo Muschat, com o Csound detectado. Em erro, -2 em saida.txt e a mensagem em
;;; erro.txt. Não contém código do OMChroma: só chamadas às funções públicas.
;;;
;;; Uso no OpenMusic: Lisp > Load File > este arquivo, ao lado de entrada.txt.

(in-package :om)

(load-lib-for '("OMChroma"))

(defun muschat-chroma-ler (arquivo)
  "Só números: *read-eval* desligado, reais lidos em dupla precisão, qualquer outra coisa interrompe."
  (let ((*read-eval* nil) (*read-default-float-format* 'double-float))
    (with-open-file (s arquivo :direction :input)
      (loop for x = (read s nil :fim)
            until (eq x :fim)
            do (unless (numberp x) (error "entrada.txt com algo que não é número: ~S" x))
            collect x))))

(defun muschat-chroma-gravar (pasta nome texto)
  (with-open-file (s (merge-pathnames nome pasta) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string texto s)))

(defun muschat-chroma-copiar (origem destino)
  (with-open-file (entrada origem :direction :input)
    (with-open-file (saida destino :direction :output :if-exists :supersede :if-does-not-exist :create)
      (loop for linha = (read-line entrada nil nil)
            while linha do (write-line linha saida)))))

(defun muschat-sintese-aditiva (pasta)
  (handler-case
      (let* ((numeros (muschat-chroma-ler (merge-pathnames "entrada.txt" pasta)))
             (n (round (pop numeros))))
        (unless (= (length numeros) (* 4 n))
          (error "entrada.txt anuncia ~D componentes e traz ~D números" n (length numeros)))
        (let* ((linhas (loop for resto on numeros by #'cddddr collect (subseq resto 0 4)))
               (evento (om-make-array 'add-1 n 0 nil
                                      :e-dels (mapcar #'first linhas)
                                      :durs (mapcar #'second linhas)
                                      :freq (mapcar #'third linhas)
                                      :amp (mapcar #'fourth linhas)))
               (arquivos (synthesize evento :name "muschat" :run nil)))
          (unless (and (first arquivos) (second arquivos))
            (error "o OMChroma não devolveu os arquivos .orc/.sco: ~S" arquivos))
          (muschat-chroma-copiar (first arquivos) (merge-pathnames "muschat.orc" pasta))
          (muschat-chroma-copiar (second arquivos) (merge-pathnames "muschat.sco" pasta))
          (muschat-chroma-gravar pasta "saida.txt" (format nil "~D~%" n))
          n))
    (error (e)
      (muschat-chroma-gravar pasta "erro.txt" (format nil "~A~%" e))
      (muschat-chroma-gravar pasta "saida.txt" (format nil "-2~%")))))

;; A pasta da tarefa acompanha o arquivo carregado, independentemente do workspace.
(muschat-sintese-aditiva (make-pathname :name nil :type nil :defaults *load-truename*))
