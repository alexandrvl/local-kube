# Recreate and adopt the lab nodes. GitOps stays in gitops/ and Argo CD.
export PATH := $(HOME)/.local/bin:$(PATH)
export ANSIBLE_CONFIG := ansible/ansible.cfg

.PHONY: plan apply destroy inventory ssh-access ansible adopt

plan:
	tofu -chdir=tofu plan -input=false -detailed-exitcode

apply:
	tofu -chdir=tofu init -input=false
	tofu -chdir=tofu apply -input=false -auto-approve
	$(MAKE) ssh-access
	$(MAKE) inventory
	$(MAKE) ansible

adopt:
	tofu -chdir=tofu init -input=false
	tofu -chdir=tofu import 'multipass_instance.node["k3s-server"]' k3s-server
	tofu -chdir=tofu import 'multipass_instance.node["k3s-agent"]' k3s-agent

ssh-access:
	tofu -chdir=tofu init -input=false
	bash scripts/ensure-ssh-access.sh

inventory:
	python3 scripts/render-inventory.py

ansible:
	ansible-playbook ansible/site.yml

destroy:
	python3 scripts/plan-guard.py destroy
	tofu -chdir=tofu destroy -input=false -auto-approve
