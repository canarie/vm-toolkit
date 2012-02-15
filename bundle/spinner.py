import sys, time

def draw_spinner(delay=0.2):
	while True:
		for c in '/-\\|':
			sys.stdout.write(c)
			sys.stdout.flush()
			time.sleep(delay)
			sys.stdout.write('\r')

draw_spinner()
