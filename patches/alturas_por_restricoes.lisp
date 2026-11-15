;;; Muschat — patch "alturas_por_restricoes" (autoria do projeto Muschat; chama ClusterEngine e as regras
;;; R-pitches-one-voice, R-pitch-pitch e HR-pitches-one-voice da biblioteca Cluster Engine, de Örjan Sandred, no
;;; porte para OpenMusic de Paulo Raposo).
;;;
;;; Lê <pasta>/entrada.txt (só inteiros):
;;;   V S U C H M        vozes, salto máximo (cents), sem uníssono (0/1), sem cruzamento (0/1), notas comuns (0/1),
;;;                      número de variáveis do solver
;;;   NUM DEN            métrica
;;;   por voz: E n1 d1 n2 d2 ...   o ritmo fixo (E eventos; n/d da semibreve; n negativo = pausa)
;;;            D a1 a2 ...         as alturas possíveis da voz (midicents)
;;; O ritmo de cada voz entra como domínio de um motivo só (fica travado); o solver escolhe as alturas. As vozes
;;; vêm de cima para baixo (ordem das partes na grade): é o que "sem cruzamento" supõe.
;;; Grava em <pasta>/saida.txt: V e, para cada voz, k seguido das k alturas das notas, na ordem; -1 se não houver
;;; solução. Em erro, -2 em saida.txt e a mensagem em erro.txt. Não contém código do Cluster Engine: só chamadas.
;;;
;;; Uso no OpenMusic: Lisp > Load File > este arquivo, ao lado de entrada.txt.

(in-package :om)

(load-lib-for '("Cluster-Engine"))

(defun muschat-ce-ler (arquivo)
  "Só números inteiros: *read-eval* desligado e qualquer outra coisa interrompe."
  (let ((*read-eval* nil))
    (with-open-file (s arquivo :direction :input)
      (loop for x = (read s nil :fim)
            until (eq x :fim)
            do (unless (integerp x) (error "entrada.txt com algo que não é inteiro: ~S" x))
            collect x))))

(defun muschat-ce-gravar (pasta nome texto)
  (with-open-file (s (merge-pathnames nome pasta) :direction :output :if-exists :supersede :if-does-not-exist :create)
    (write-string texto s)))

(defun muschat-ce-vozes (numeros v)
  "-> lista de (ritmo dominio); sobra de números é erro (entrada inconsistente)."
  (let ((vozes (loop repeat v
                     collect (let* ((e (pop numeros))
                                    (ritmo (loop repeat e collect (let ((n (pop numeros)) (d (pop numeros))) (/ n d))))
                                    (d (pop numeros)))
                               (when (< (length numeros) d) (error "entrada.txt termina antes do domínio anunciado"))
                               (list ritmo (loop repeat d collect (pop numeros)))))))
    (when numeros (error "entrada.txt tem ~D números a mais" (length numeros)))
    vozes))

(defun muschat-ce-ataques (ritmo)
  (count-if #'plusp ritmo))

;; As regras são funções globais com nome, nunca lambdas: o Cluster Engine compila cada regra, e o LispWorks não
;; compila uma closure ("... is a lexical closure", visto no OM real). O salto vem por variável especial.
(defvar *muschat-ce-salto* 1200)

(defun muschat-ce-salto-ok (a b)
  (<= (abs (- b a)) *muschat-ce-salto*))

(defun muschat-ce-repete (a b)
  "Heurística das notas comuns: peso 1 quando a altura se repete."
  (if (= a b) 1 0))

(defun muschat-ce-decrescente (alturas)
  "Sem cruzamento: as vozes vêm de cima para baixo; pausas (nil) não contam."
  (let ((soando (remove nil alturas)))
    (or (null soando) (apply #'>= soando))))

(defun muschat-ce-sem-unissono (alturas)
  (let ((soando (remove nil alturas)))
    (= (length soando) (length (remove-duplicates soando)))))

(defun muschat-alturas-por-restricoes (pasta)
  (handler-case
      (let* ((numeros (muschat-ce-ler (merge-pathnames "entrada.txt" pasta)))
             (v (pop numeros)) (salto (pop numeros)) (unissono (pop numeros)) (cruzamento (pop numeros))
             (comuns (pop numeros)) (variaveis (pop numeros))
             (metrica (list (pop numeros) (pop numeros)))
             (vozes (muschat-ce-vozes numeros v))
             (dominios (loop for (ritmo dominio) in vozes
                             append (list (list ritmo) (mapcar #'list dominio))))
             (todas (loop for i below v collect i))
             (*muschat-ce-salto* salto)
             (regras
               (append
                ;; cada regra devolve uma lista de regras; a lista final tem de ser plana (filter-rules-from-input)
                (cluster-engine::r-pitches-one-voice #'muschat-ce-salto-ok todas :pitches)
                (when (and (= unissono 1) (> v 1))
                  (cluster-engine::r-pitch-pitch #'muschat-ce-sem-unissono todas '(0) :all :exclude-gracenotes :pitch))
                (when (and (= cruzamento 1) (> v 1))
                  (cluster-engine::r-pitch-pitch #'muschat-ce-decrescente todas '(0) :all :exclude-gracenotes :pitch))
                (when (= comuns 1)
                  (cluster-engine::hr-pitches-one-voice #'muschat-ce-repete todas :pitches))))
             ;; rnd? = t: sem ele o solver escolhe sempre o primeiro candidato (as alturas mais graves)
             ;; shortcut: uma métrica só (a do primeiro compasso); nenhuma regra lê a métrica, evoluir se alguma ler
             (resultado (cluster-engine::clusterengine variaveis t nil regras (list metrica) dominios))
             ;; sem solução o solver devolve uma solução parcial (não nil): falta altura em alguma voz
             (alturas (loop for i below v collect (remove nil (flat (nth (1+ (* 2 i)) resultado)))))
             (completa (loop for (ritmo nil) in vozes for a in alturas
                             always (>= (length a) (muschat-ce-ataques ritmo)))))
        (muschat-ce-gravar
         pasta "saida.txt"
         (if completa
             (format nil "~D~{ ~D~{ ~D~}~}~%" v (loop for a in alturas append (list (length a) a)))
             (format nil "-1~%")))
        resultado)
    (error (e)
      (muschat-ce-gravar pasta "erro.txt" (format nil "~A~%" e))
      (muschat-ce-gravar pasta "saida.txt" (format nil "-2~%")))))

;; A pasta da tarefa acompanha o arquivo carregado, independentemente do workspace.
(muschat-alturas-por-restricoes (make-pathname :name nil :type nil :defaults *load-truename*))
